"""Entrypoint do serviço de scraper.

Roda iPhone via APScheduler, a cada `scrape_interval_minutes` -- uma
rodada por região configurada em `settings.iphone_regioes` (sequencial,
com um delay pequeno entre regiões, mesmo espírito do delay entre
páginas: scraper deliberadamente discreto). monitor/computador (mercado
se mostrou ineficaz) pararam de ser agendados, mas `rodar_coleta_monitor`/
`rodar_coleta_computador` continuam definidas aqui -- é fácil reativar se
precisar, e dado histórico dessas categorias continua no banco.

Toda rodada (de qualquer categoria/região) usa a mesma orquestração
(`rodar_coleta`, parametrizada) — só muda a URL de busca, qual schema
(`MonitorAd`/`IphoneAd`/`ComputadorAd`) interpreta o JSON da OLX, e pra
onde manda o alerta de oportunidade. Cada rodada pode interromper sem
afetar as outras nem a próxima (o agendador continua rodando mesmo se uma
rodada específica falhar) — inclusive gravar no banco: um erro ali avisa
por Telegram (sempre no chat pessoal, nunca num grupo público) e desiste
da rodada, do mesmo jeito que fetch/parse/sanidade já faziam, em vez de
sumir sem ninguém notar:

    fetch -> parse -> checkpoint de sanidade -> grava -> alertas
"""

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from auditoria_ia import auditar_anuncio
from common.config import settings
from common.schema import ComputadorAd, IphoneAd, MonitorAd
from common.storage import contagem_media_ultimas_coletas, eh_minimo_historico, init_db, upsert_ads
from diff import separar_novidades
from fetcher import FetchError, fetch_html, url_pagina
from notifier import enviar_telegram
from parser import build_ads, extract_ads
from resumo_diario import enviar_resumo_diario
from sanity import checar_sanidade
from common.stats import Avaliacao, avaliar, grava_snapshot_diario

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scraper")


def _linha_extra(ad) -> str:
    """Detalhe específico de categoria que muda a decisão de compra mas
    não é forte o bastante pra excluir o anúncio da mediana como 'com
    defeito' -- é informação, não veto. getattr defensivo porque cada
    campo só existe numa categoria por vez."""
    saude = getattr(ad, "saude_bateria", None)
    if saude:
        return f"\nBateria: {saude}"
    if getattr(ad, "inclui_monitor", False):
        return "\n🖥️ Inclui monitor (marca/tamanho não informados — confira no anúncio)"
    return ""


def _msg_novo(ad, av: Avaliacao) -> str:
    return (
        f"💰 Oportunidade (novo anúncio) — {ad.titulo}\n"
        f"Anunciado: R$ {av.preco:.0f} — {ad.municipio or '?'} — {ad.condicao or 'condição não informada'}"
        f"{_linha_extra(ad)}\n"
        f"Após negociar (~{settings.desconto_negociacao_esperado:.0%}): "
        f"R$ {av.custo_apos_negociacao:.0f} · mediana do grupo: R$ {av.mediana:.0f}\n"
        f"Margem estimada: R$ {av.margem_rs:.0f} ({av.margem_pct:.0%})\n"
        f"{ad.url}"
    )


def _msg_queda(ad, av: Avaliacao, preco_anterior: float, novo_minimo: bool) -> str:
    estrela = " 🔻 mínimo histórico" if novo_minimo else ""
    return (
        f"📉 Baixou de preço e virou oportunidade — {ad.titulo}\n"
        f"R$ {preco_anterior:.0f} → R$ {av.preco:.0f}{estrela} — {ad.municipio or '?'} — "
        f"{ad.condicao or 'condição não informada'}"
        f"{_linha_extra(ad)}\n"
        f"Após negociar (~{settings.desconto_negociacao_esperado:.0%}): "
        f"R$ {av.custo_apos_negociacao:.0f} · mediana do grupo: R$ {av.mediana:.0f}\n"
        f"Margem estimada: R$ {av.margem_rs:.0f} ({av.margem_pct:.0%})\n"
        f"{ad.url}"
    )


def rodar_coleta(
    *, nome: str, search_url: str, ad_class, categoria: str, uf: str,
    chat_id_oportunidade: str | None = None,
) -> None:
    """Uma rodada completa pra UMA categoria+região. `nome` só aparece em
    log e nas mensagens de erro, pra diferenciar qual rodada falhou quando
    várias rodam no mesmo processo. `uf` escopa sanidade/upsert pra essa
    região (ver docstrings de contagem_media_ultimas_coletas/upsert_ads em
    common/storage.py). `chat_id_oportunidade` é só pro alerta de
    oportunidade (novo anúncio/queda de preço) -- erro/sanidade sempre vai
    pro chat pessoal (enviar_telegram sem chat_id explícito), nunca pra um
    grupo público."""
    logger.info("Iniciando coleta (%s)...", nome)

    raw_items: list[dict] = []
    try:
        for pagina in range(1, settings.max_paginas + 1):
            if pagina > 1:
                time.sleep(1)  # teto subiu (5->10 paginas); intervalo curto entre elas
            html = fetch_html(url_pagina(search_url, pagina))
            pagina_raw = extract_ads(html)
            reais = [i for i in pagina_raw if "listId" in i]
            raw_items.extend(pagina_raw)
            if len(reais) < 50:
                break  # última página de resultado real
    except FetchError as e:
        logger.error(str(e))
        enviar_telegram(f"⚠️ Scraper ({nome}): falha ao buscar a página da OLX.\n{e}")
        return

    try:
        ads = build_ads(raw_items, ad_class)
    except ValueError as e:
        logger.error(str(e))
        enviar_telegram(
            f"⚠️ Scraper ({nome}) possivelmente quebrado: não encontrei o JSON de "
            f"anúncios no HTML. A OLX pode ter mudado o front-end.\nDetalhe: {e}"
        )
        return

    media_historica = contagem_media_ultimas_coletas(categoria=categoria, uf=uf)
    sanidade = checar_sanidade(ads, media_historica)
    if not sanidade.ok:
        logger.warning("Checkpoint de sanidade (%s) falhou: %s", nome, sanidade.motivo)
        enviar_telegram(f"⚠️ Scraper ({nome}) possivelmente quebrado: {sanidade.motivo}")
        return

    try:
        resultado = upsert_ads(ads, uf=uf)
    except Exception as e:
        logger.error("Falha ao gravar a coleta (%s) no banco: %s", nome, e)
        enviar_telegram(f"⚠️ Scraper ({nome}): falha ao gravar a coleta no banco.\n{e}")
        return

    novos, quedas = separar_novidades(ads, resultado)
    logger.info(
        "Coleta ok (%s): %d anúncios (%d novos, %d com queda de preço).",
        nome, len(ads), len(novos), len(quedas),
    )

    try:
        grava_snapshot_diario(categoria)
    except Exception as e:
        # Nunca deixa a base de dado do histórico de preço derrubar a rodada
        # nem os alertas -- mesmo espírito de notifier.py: uma camada a mais
        # não pode quebrar o que já funcionava antes dela existir.
        logger.warning("Falha ao gravar snapshot diário (%s): %s", nome, e)

    for ad in novos:
        try:
            auditar_anuncio(ad)
        except Exception as e:
            # Mesmo espírito do try/except em volta de grava_snapshot_diario
            # acima: uma camada a mais (e paga, chamando API externa) não
            # pode derrubar a rodada nem os alertas de oportunidade abaixo.
            logger.warning("Falha ao auditar anúncio %s via IA (%s): %s", ad.listing_id, nome, e)

        av = avaliar(ad.preco, ad.categoria, ad.grupo, ad.condicao, ad.titulo)
        if av and av.eh_oportunidade:
            enviar_telegram(_msg_novo(ad, av), chat_id=chat_id_oportunidade)

    for ad in quedas:
        av = avaliar(ad.preco, ad.categoria, ad.grupo, ad.condicao, ad.titulo)
        if av and av.eh_oportunidade:
            _, preco_anterior = resultado.quedas[ad.listing_id]
            novo_minimo = eh_minimo_historico(ad.listing_id, ad.preco)
            enviar_telegram(_msg_queda(ad, av, preco_anterior, novo_minimo), chat_id=chat_id_oportunidade)


def rodar_coleta_monitor() -> None:
    """Não é mais agendada em main() -- mercado se mostrou ineficaz. Fica
    definida (não removida) pra reativar fácil se precisar; dado histórico
    continua no banco."""
    rodar_coleta(nome="monitor", search_url=settings.olx_search_url, ad_class=MonitorAd, categoria="monitor", uf="ES")


def rodar_coleta_computador() -> None:
    """Mesma situação de rodar_coleta_monitor acima."""
    rodar_coleta(
        nome="computador", search_url=settings.computador_search_url,
        ad_class=ComputadorAd, categoria="computador", uf="ES",
    )


def rodar_coleta_iphone_todas_regioes() -> None:
    """1 rodada por região em `settings.iphone_regioes`, sequencial (nunca
    paralelo de verdade -- ver docstring do módulo), com um delay pequeno
    entre elas. Sem IPHONE_REGIOES no .env, é só a região pessoal (ES)."""
    for i, regiao in enumerate(settings.iphone_regioes):
        if i > 0:
            time.sleep(settings.intervalo_entre_regioes_segundos)
        rodar_coleta(
            nome=f"iPhone ({regiao.nome})",
            search_url=settings.iphone_search_url_template.format(uf=regiao.uf.lower()),
            ad_class=IphoneAd,
            categoria="iphone",
            uf=regiao.uf,
            chat_id_oportunidade=regiao.chat_id,
        )


def main() -> None:
    init_db()
    logger.info("Banco pronto em %s", settings.db_path)

    scheduler = BlockingScheduler(timezone=timezone.utc)
    scheduler.add_job(
        rodar_coleta_iphone_todas_regioes,
        "interval",
        minutes=settings.scrape_interval_minutes,
        next_run_time=datetime.now(timezone.utc),  # roda uma vez imediatamente
    )
    scheduler.add_job(
        enviar_resumo_diario,
        "cron",
        hour=settings.resumo_diario_hora_utc,
        minute=0,
        timezone=timezone.utc,
    )
    logger.info(
        "Agendador ativo: iPhone (%d região(ões)), a cada %d min. "
        "Resumo diário às %02d:00 UTC (se resumo_diario_ativo=True).",
        len(settings.iphone_regioes), settings.scrape_interval_minutes, settings.resumo_diario_hora_utc,
    )
    scheduler.start()


if __name__ == "__main__":
    main()

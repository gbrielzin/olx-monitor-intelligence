"""Entrypoint do serviço de scraper.

Roda em loop via APScheduler, a cada `scrape_interval_minutes`. Cada
rodada segue sempre a mesma ordem, e cada etapa pode interromper a
rodada sem afetar a próxima (o agendador continua rodando mesmo se uma
rodada específica falhar) — inclusive gravar no banco: um erro ali
avisa por Telegram e desiste da rodada, do mesmo jeito que fetch/parse/
sanidade já faziam, em vez de sumir sem ninguém notar:

    fetch -> parse -> checkpoint de sanidade -> grava -> alertas
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from common.config import settings
from common.schema import MonitorAd
from common.storage import contagem_media_ultimas_coletas, eh_minimo_historico, init_db, upsert_ads
from diff import separar_novidades
from fetcher import FetchError, fetch_html, url_pagina
from notifier import enviar_telegram
from parser import build_ads, extract_ads
from sanity import checar_sanidade
from common.stats import Avaliacao, avaliar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scraper")


def _msg_novo(ad: MonitorAd, av: Avaliacao) -> str:
    return (
        f"💰 *Oportunidade (novo anúncio)* — {ad.titulo}\n"
        f"Anunciado: R$ {av.preco:.0f} — {ad.municipio or '?'}\n"
        f"Após negociar (~{settings.desconto_negociacao_esperado:.0%}): "
        f"R$ {av.custo_apos_negociacao:.0f} · mediana do grupo: R$ {av.mediana:.0f}\n"
        f"Margem estimada: R$ {av.margem_rs:.0f} ({av.margem_pct:.0%})\n"
        f"{ad.url}"
    )


def _msg_queda(ad: MonitorAd, av: Avaliacao, preco_anterior: float, novo_minimo: bool) -> str:
    estrela = " 🔻 mínimo histórico" if novo_minimo else ""
    return (
        f"📉 *Baixou de preço e virou oportunidade* — {ad.titulo}\n"
        f"R$ {preco_anterior:.0f} → R$ {av.preco:.0f}{estrela} — {ad.municipio or '?'}\n"
        f"Após negociar (~{settings.desconto_negociacao_esperado:.0%}): "
        f"R$ {av.custo_apos_negociacao:.0f} · mediana do grupo: R$ {av.mediana:.0f}\n"
        f"Margem estimada: R$ {av.margem_rs:.0f} ({av.margem_pct:.0%})\n"
        f"{ad.url}"
    )


def rodar_coleta() -> None:
    logger.info("Iniciando coleta...")

    raw_items: list[dict] = []
    try:
        for pagina in range(1, settings.max_paginas + 1):
            html = fetch_html(url_pagina(settings.olx_search_url, pagina))
            pagina_raw = extract_ads(html)
            reais = [i for i in pagina_raw if "listId" in i]
            raw_items.extend(pagina_raw)
            if len(reais) < 50:
                break  # última página de resultado real
    except FetchError as e:
        logger.error(str(e))
        enviar_telegram(f"⚠️ Scraper: falha ao buscar a página da OLX.\n{e}")
        return

    try:
        ads = build_ads(raw_items)
    except ValueError as e:
        logger.error(str(e))
        enviar_telegram(
            "⚠️ Scraper possivelmente quebrado: não encontrei o JSON de "
            f"anúncios no HTML. A OLX pode ter mudado o front-end.\nDetalhe: {e}"
        )
        return

    media_historica = contagem_media_ultimas_coletas()
    sanidade = checar_sanidade(ads, media_historica)
    if not sanidade.ok:
        logger.warning("Checkpoint de sanidade falhou: %s", sanidade.motivo)
        enviar_telegram(f"⚠️ Scraper possivelmente quebrado: {sanidade.motivo}")
        return

    try:
        resultado = upsert_ads(ads)
    except Exception as e:
        logger.error("Falha ao gravar a coleta no banco: %s", e)
        enviar_telegram(f"⚠️ Scraper: falha ao gravar a coleta no banco.\n{e}")
        return

    novos, quedas = separar_novidades(ads, resultado)
    logger.info(
        "Coleta ok: %d anúncios (%d novos, %d com queda de preço).",
        len(ads), len(novos), len(quedas),
    )

    for ad in novos:
        av = avaliar(ad.preco, ad.marca, ad.tipo_monitor)
        if av and av.eh_oportunidade:
            enviar_telegram(_msg_novo(ad, av))

    for ad in quedas:
        av = avaliar(ad.preco, ad.marca, ad.tipo_monitor)
        if av and av.eh_oportunidade:
            _, preco_anterior = resultado.quedas[ad.listing_id]
            novo_minimo = eh_minimo_historico(ad.listing_id, ad.preco)
            enviar_telegram(_msg_queda(ad, av, preco_anterior, novo_minimo))


def main() -> None:
    init_db()
    logger.info("Banco pronto em %s", settings.db_path)

    scheduler = BlockingScheduler(timezone=timezone.utc)
    scheduler.add_job(
        rodar_coleta,
        "interval",
        minutes=settings.scrape_interval_minutes,
        next_run_time=datetime.now(timezone.utc),  # roda uma vez imediatamente
    )
    logger.info("Agendador ativo: a cada %d min.", settings.scrape_interval_minutes)
    scheduler.start()


if __name__ == "__main__":
    main()

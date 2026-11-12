"""Mensagens de alerta e o disparo delas -- saiu de `main.py` pra ser
reaproveitado, sem cópia, pelo flow do Prefect (`flows/pipeline_olx.py`).
O texto das mensagens e as regras (auditoria IA, `avaliar`, mínimo
histórico) são exatamente os de antes; só mudou de arquivo."""

import logging

from auditoria_ia import auditar_anuncio
from common.config import settings
from common.stats import Avaliacao, avaliar
from common.storage import ColetaResultado, eh_minimo_historico
from notifier import enviar_telegram

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


def _linhas_margem(av: Avaliacao) -> str:
    """Margem em três leituras, não uma: sobre o custo (a mais alta), sobre
    a venda e num cenário conservador (revende abaixo da mediana pedida).
    Margem acima de `margem_suspeita` vem marcada -- preço muito baixo
    costuma ser defeito escondido, não pechincha."""
    texto = (
        f"Margem estimada: R$ {av.margem_rs:.0f} ({av.margem_pct:.0%} sobre o custo · "
        f"{av.margem_sobre_venda_pct:.0%} sobre a venda)\n"
        f"Cenário conservador (revenda -{settings.desconto_revenda_esperado:.0%}): "
        f"R$ {av.margem_conservadora_rs:.0f}\n"
    )
    if av.suspeita:
        texto += "⚠️ Margem alta demais pra ser normal — confira descrição, bateria e bloqueio antes de ir\n"
    return texto


def _msg_novo(ad, av: Avaliacao) -> str:
    return (
        f"💰 Oportunidade (novo anúncio) — {ad.titulo}\n"
        f"Anunciado: R$ {av.preco:.0f} — {ad.municipio or '?'} — {ad.condicao or 'condição não informada'}"
        f"{_linha_extra(ad)}\n"
        f"Após negociar (~{settings.desconto_negociacao_esperado:.0%}): "
        f"R$ {av.custo_apos_negociacao:.0f} · mediana do grupo: R$ {av.mediana:.0f}\n"
        f"{_linhas_margem(av)}"
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
        f"{_linhas_margem(av)}"
        f"{ad.url}"
    )


def disparar_alertas(
    novos: list, quedas: list, resultado: ColetaResultado, *, nome: str,
    chat_id_oportunidade: str | None = None,
) -> int:
    """Audita (se a IA estiver ligada) e avisa no Telegram os anúncios novos
    e as quedas de preço que viraram oportunidade. Devolve quantos alertas
    de oportunidade foram enviados."""
    enviados = 0
    for ad in novos:
        try:
            auditar_anuncio(ad)
        except Exception as e:
            # Mesmo espírito do try/except em volta de grava_snapshot_diario
            # (main.rodar_coleta): uma camada a mais (e paga, chamando API externa) não
            # pode derrubar a rodada nem os alertas de oportunidade abaixo.
            logger.warning("Falha ao auditar anúncio %s via IA (%s): %s", ad.listing_id, nome, e)

        av = avaliar(ad.preco, ad.categoria, ad.grupo, ad.condicao, ad.titulo, getattr(ad, "modelo", None))
        if av and av.eh_oportunidade:
            enviar_telegram(_msg_novo(ad, av), chat_id=chat_id_oportunidade)
            enviados += 1

    for ad in quedas:
        av = avaliar(ad.preco, ad.categoria, ad.grupo, ad.condicao, ad.titulo, getattr(ad, "modelo", None))
        if av and av.eh_oportunidade:
            _, preco_anterior = resultado.quedas[ad.listing_id]
            novo_minimo = eh_minimo_historico(ad.listing_id, ad.preco)
            enviar_telegram(_msg_queda(ad, av, preco_anterior, novo_minimo), chat_id=chat_id_oportunidade)
            enviados += 1
    return enviados

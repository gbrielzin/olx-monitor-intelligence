"""Diff contra o estado anterior.

O ponto de ter isso separado do storage.py: 'anúncio novo' e 'anúncio
com queda de preço' são decisões de negócio (o que dispara alerta),
'gravar o estado no banco' é decisão de infraestrutura. `upsert_ads()`
já calcula os dois grupos (por id) a partir do estado anterior; este
módulo só traduz de volta pros objetos MonitorAd que o resto do
pipeline (mensagem do Telegram, log) usa.
"""

from common.schema import MonitorAd
from common.storage import ColetaResultado


def separar_novidades(
    ads: list[MonitorAd], resultado: ColetaResultado
) -> tuple[list[MonitorAd], list[MonitorAd]]:
    """Retorna (novos, com_queda_de_preco) — os dois gatilhos de alerta."""
    novos = [a for a in ads if a.listing_id in resultado.novos]
    quedas = [a for a in ads if a.listing_id in resultado.quedas]
    return novos, quedas

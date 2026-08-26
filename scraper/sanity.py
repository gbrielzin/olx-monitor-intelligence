"""Checkpoint de sanidade — a rede de segurança contra a OLX mudar o
front-end sem avisar.

Roda depois do parse e antes do save_ads(). Se algo aqui falhar, a
coleta é descartada (nada é gravado) e um alerta sai pelo Telegram.
Fail loud, não fail silent: é melhor perder uma rodada de dados do que
encher o banco com linhas de preço=None por dias até alguém notar o
dashboard parado.
"""

from dataclasses import dataclass

from common.config import settings
from common.schema import MonitorAd


@dataclass
class SanityResult:
    ok: bool
    motivo: str = ""


def checar_sanidade(ads: list[MonitorAd], media_historica: float | None) -> SanityResult:
    if not ads:
        return SanityResult(False, "zero anúncios extraídos — provável quebra total do parser")

    com_preco = sum(1 for a in ads if a.preco is not None)
    ratio_preco = com_preco / len(ads)
    if ratio_preco < settings.min_price_field_ratio:
        return SanityResult(
            False,
            f"apenas {ratio_preco:.0%} dos anúncios com preço detectado "
            f"(esperado >= {settings.min_price_field_ratio:.0%})",
        )

    if media_historica is not None and len(ads) < media_historica * settings.min_ads_ratio:
        return SanityResult(
            False,
            f"{len(ads)} anúncios coletados vs média histórica de {media_historica:.0f} "
            f"— queda acima do limite tolerado",
        )

    return SanityResult(True)

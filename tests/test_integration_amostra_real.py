"""Terceira rodada de teste: um trecho mais próximo do payload real da
OLX (com acentos, propaganda misturada no array, desconto ativo e
múltiplas propriedades), montado a partir da estrutura observada numa
página real de busca — não é mais um fixture artificial mínimo."""

import json

from scraper.parser import parse_ads


def _escapar_como_js_string(obj) -> str:
    """Simula como o Next.js serializa o array dentro de
    self.__next_f.push([1, "..."]) -- aspas do JSON viram \\"."""
    bruto = json.dumps(obj, ensure_ascii=False)
    return bruto.replace('"', '\\"')


def _montar_html(ads: list[dict]) -> str:
    ads_escapado = _escapar_como_js_string(ads)
    return (
        '<script>self.__next_f.push([1,"0:{\\"algumOutroCampo\\":true},'
        f'\\"ads\\":{ads_escapado},\\"totalOfAds\\":517"])</script>'
    )


def test_amostra_realista_end_to_end():
    ads_brutos = [
        {"advertisingId": "advertising-desktop-listing-native-direct", "deviceType": "desktop"},
        {
            "listId": 1528250081,
            "subject": "Monitor Dell 24' P2418D QHD com hub USB em perfeito estado",
            "priceValue": "R$ 1.250",
            "oldPrice": None,
            "url": "https://es.olx.com.br/norte-do-espirito-santo/informatica/monitores/x-1528250081",
            "date": 1787313137,
            "locationDetails": {
                "municipality": "Vila Velha",
                "neighbourhood": "Praia de Itaparica",
                "uf": "ES",
            },
            "properties": [
                {"name": "info_monitors_brand", "value": "Dell"},
                {"name": "info_monitors_condition", "value": "Usado - Excelente"},
                {"name": "info_monitors_inches", "value": "24 polegadas"},
                {"name": "info_monitors_refresh_rate", "value": "60 Hz"},
                {"name": "info_monitors_screen_type", "value": "LCD IPS"},
                {"name": "info_monitors_type", "value": "Monitor"},
            ],
            "olxPay": {"transactionalSellerName": "guilherme", "transactionalSellerRating": 5.0},
        },
        {
            "listId": 1528094775,
            "subject": "Monitor Gamer AOC 24? IPS Full HD 180hz 24G30E",
            "priceValue": "R$ 750",
            "oldPrice": "R$ 800",
            "url": "https://es.olx.com.br/x-1528094775",
            "date": 1787365550,
            "locationDetails": {
                "municipality": "Cachoeiro de Itapemirim",
                "neighbourhood": "Amarelo",
                "uf": "ES",
            },
            "properties": [
                {"name": "info_monitors_brand", "value": "AOC"},
                {"name": "info_monitors_refresh_rate", "value": "144 Hz ou maior"},
                {"name": "info_monitors_features", "value": "Possui DisplayPort, Possui HDMI"},
            ],
        },
    ]

    html = _montar_html(ads_brutos)
    ads = parse_ads(html)

    assert len(ads) == 2  # o placeholder de propaganda foi descartado

    dell = next(a for a in ads if a.listing_id == 1528250081)
    assert dell.marca == "Dell"
    assert dell.municipio == "Vila Velha"  # acento sobreviveu ao round-trip de escape
    assert dell.bairro == "Praia de Itaparica"
    assert dell.preco == 1250.0
    assert dell.preco_antigo is None
    # "60 Hz" não está no título, mas vem exato em info_monitors_refresh_rate
    # (não é faixa tipo "144 Hz ou maior") — o fallback de schema.py recupera
    # esse valor em vez de descartá-lo.
    assert dell.hz_exato == 60

    aoc = next(a for a in ads if a.listing_id == 1528094775)
    assert aoc.preco == 750.0
    assert aoc.preco_antigo == 800.0
    assert aoc.hz_exato == 180  # veio do título "180hz", não da faixa "144 Hz ou maior"
    assert aoc.municipio == "Cachoeiro de Itapemirim"

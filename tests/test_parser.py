import json

import pytest

from scraper.parser import extract_ads, parse_ads


def _fake_html(ads_json: str) -> str:
    """Simula o formato real: o array vive dentro de uma string JS, então
    as aspas do JSON vêm escapadas (\\"ads\\":[...])."""
    corpo = ads_json[1:-1]  # tira os colchetes externos '[' e ']'
    escaped_corpo = corpo.replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"lixo antes \\"ads\\":[{escaped_corpo}] lixo depois"])</script>'


def test_extract_ads_simples():
    ads = [{"listId": 1, "subject": "Monitor teste"}]
    html = _fake_html(json.dumps(ads))
    assert extract_ads(html) == ads


def test_extract_ads_com_colchetes_aninhados():
    """Garante que a extração não para no primeiro ']' encontrado — o bug
    mais provável de uma implementação ingênua com regex guloso."""
    ads = [
        {
            "listId": 1,
            "subject": "Monitor [promocao]",
            "properties": [{"name": "info_monitors_brand", "value": "AOC"}],
        }
    ]
    html = _fake_html(json.dumps(ads))
    assert extract_ads(html) == ads


def test_extract_ads_chave_ausente_levanta_erro_claro():
    with pytest.raises(ValueError, match="não encontrada"):
        extract_ads("<html>sem nada de util aqui</html>")


def test_parse_ads_ignora_placeholder_de_propaganda():
    raw = [
        {"advertisingId": "banner-1", "deviceType": "desktop"},
        {
            "listId": 42,
            "subject": "Monitor Gamer AOC 24G2S/BK 165Hz",
            "priceValue": "R$ 750",
            "url": "https://es.olx.com.br/x-42",
            "date": 1787000000,
            "locationDetails": {"municipality": "Vitoria", "neighbourhood": "Jardim da Penha"},
            "properties": [
                {"name": "info_monitors_brand", "value": "AOC"},
                {"name": "info_monitors_refresh_rate", "value": "144 Hz ou maior"},
            ],
        },
    ]
    html = _fake_html(json.dumps(raw))
    ads = parse_ads(html)
    assert len(ads) == 1
    assert ads[0].listing_id == 42
    assert ads[0].hz_exato == 165  # veio do titulo, nao da faixa
    assert ads[0].preco == 750.0


def test_parse_ads_item_malformado_nao_derruba_o_lote():
    raw = [
        {"listId": 1, "subject": "Ok", "url": "https://x", "date": 1787000000, "priceValue": "R$ 100"},
        {"listId": 2, "subject": "Sem url nem data"},
    ]
    html = _fake_html(json.dumps(raw))
    ads = parse_ads(html)
    assert len(ads) == 1
    assert ads[0].listing_id == 1

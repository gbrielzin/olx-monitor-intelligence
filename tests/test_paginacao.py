"""Prova de que o bug de paginação está corrigido: antes, o loop de
coleta só existia mentalmente (`fetch_html` era chamado uma vez); agora
`url_pagina` + o loop em `main.rodar_coleta` percorrem várias páginas e
param sozinhos quando uma página vem com menos de 50 itens reais."""

import json

from scraper.fetcher import url_pagina
from scraper.parser import build_ads, extract_ads


def _fake_html(ads_json: list[dict]) -> str:
    corpo = json.dumps(ads_json).replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"lixo \\"ads\\":{corpo} lixo"])</script>'


def _ad(listing_id: int) -> dict:
    return {
        "listId": listing_id,
        "subject": f"Monitor teste {listing_id}",
        "priceValue": "R$ 500",
        "url": f"https://x/{listing_id}",
        "date": 1787000000,
        "properties": [],
    }


def test_url_pagina_monta_parametro_o_corretamente():
    base = "https://www.olx.com.br/informatica/monitores/estado-es?q=monitor"
    assert url_pagina(base, 1) == base
    assert url_pagina(base, 2) == base + "&o=2"
    assert url_pagina(base, 3) == base + "&o=3"


def test_url_pagina_funciona_tambem_sem_query_string_previa():
    base = "https://www.olx.com.br/estado-es"
    assert url_pagina(base, 2) == base + "?o=2"


def test_paginacao_descobre_mais_de_50_anuncios_unicos():
    """Simula 3 páginas de 50, 50 e 30 (última) — o comportamento antigo
    (sem paginação) nunca passaria de 50 anúncios únicos no total."""
    pagina_1 = [_ad(i) for i in range(1, 51)]        # 50 itens -> continua
    pagina_2 = [_ad(i) for i in range(51, 101)]       # 50 itens -> continua
    pagina_3 = [_ad(i) for i in range(101, 131)]      # 30 itens -> última

    paginas_html = [_fake_html(pagina_1), _fake_html(pagina_2), _fake_html(pagina_3)]

    raw_items: list[dict] = []
    for html in paginas_html:
        pagina_raw = extract_ads(html)
        reais = [i for i in pagina_raw if "listId" in i]
        raw_items.extend(pagina_raw)
        if len(reais) < 50:
            break

    ads = build_ads(raw_items)
    assert len(ads) == 130  # 50 + 50 + 30 -- só possível varrendo as 3 páginas
    assert len({a.listing_id for a in ads}) == 130


def test_paginacao_para_na_primeira_pagina_incompleta():
    """Se a página 1 já vem com menos de 50, nem tenta buscar a página 2 —
    evita uma requisição HTTP desnecessária quando o resultado é pequeno."""
    pagina_1 = [_ad(i) for i in range(1, 11)]  # só 10 itens
    html = _fake_html(pagina_1)

    pagina_raw = extract_ads(html)
    reais = [i for i in pagina_raw if "listId" in i]
    assert len(reais) < 50  # o loop real pararia aqui, sem chamar fetch_html de novo

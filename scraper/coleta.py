"""Varredura paginada de uma busca da OLX -- o loop que antes vivia dentro de
`main.rodar_coleta`. Saiu de lá pra ser reaproveitado, sem cópia, pelo flow
do Prefect (`flows/pipeline_olx.py`): os dois caminhos fazem exatamente as
mesmas requisições, no mesmo ritmo.

Para sozinho quando uma página vem com menos de 50 anúncios reais (última
página de resultado) ou ao bater `max_paginas`. 1 s de pausa entre páginas:
o scraper é deliberadamente discreto.
"""

import time

from fetcher import fetch_html, url_pagina
from parser import extract_ads

ANUNCIOS_POR_PAGINA = 50


def coletar_paginas(search_url: str, max_paginas: int, pausa=time.sleep) -> tuple[list[dict], list[str]]:
    """Devolve (itens brutos de todas as páginas, URLs buscadas). Propaga
    `FetchError` (rede/HTTP) e `ValueError` (JSON de anúncios não achado no
    HTML) pra quem chamou decidir o que fazer -- avisar no Telegram (main.py)
    ou tentar de novo (retry do Prefect)."""
    raw_items: list[dict] = []
    urls: list[str] = []
    for pagina in range(1, max_paginas + 1):
        if pagina > 1:
            pausa(1)
        url = url_pagina(search_url, pagina)
        html = fetch_html(url)
        urls.append(url)
        pagina_raw = extract_ads(html)
        raw_items.extend(pagina_raw)
        if sum(1 for i in pagina_raw if "listId" in i) < ANUNCIOS_POR_PAGINA:
            break
    return raw_items, urls

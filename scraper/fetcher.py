"""Busca o HTML bruto da OLX via requests puro.

Nada de Playwright/browser headless aqui: a OLX renderiza os anúncios
no servidor e embute os dados como JSON dentro do próprio HTML (ver
common/schema.py e parser.py), então não precisamos executar
JavaScript pra ler os dados — só pegar o HTML como ele chega, o que é
mais leve, mais rápido e deixa uma pegada bem menor do que abrir um
Chromium a cada rodada.

Se um dia a OLX passar a exigir renderização client-side pra mostrar
os anúncios, este é o único arquivo que precisa mudar — o resto do
pipeline (parser, schema, storage) não sabe nem se importa como o HTML
chegou até aqui.
"""

import requests

from common.config import settings

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": settings.user_agent,
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)


class FetchError(Exception):
    pass


def fetch_html(url: str) -> str:
    try:
        resp = _session.get(url, timeout=settings.request_timeout_seconds)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FetchError(f"Falha ao buscar {url}: {e}") from e
    return resp.text


def url_pagina(base_url: str, pagina: int) -> str:
    if pagina == 1:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}o={pagina}"

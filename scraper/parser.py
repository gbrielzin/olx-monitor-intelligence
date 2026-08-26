r"""Extrai os anúncios do JSON embutido no HTML da OLX.

O HTML carrega os dados dentro de blocos `self.__next_f.push(...)`
(protocolo de streaming do Next.js/React Server Components). Em algum
ponto desse texto existe `"ads":[ ... ]` com a lista completa de
anúncios da página — mas como esse trecho vive dentro de uma string
JavaScript, as aspas internas do JSON vêm escapadas: `\"ads\":[...]`,
não `"ads":[...]` puro.

Duas armadilhas reais, as duas cobertas aqui:
1. Um regex guloso do tipo `"ads":\[(.*?)\]` quebra porque títulos e
   descrições de anúncio às vezes têm colchete dentro (ex: "Monitor
   [PROMOCAO]") -- por isso a extração conta profundidade de colchete
   em vez de usar regex.
2. Mesmo com a extração correta, o texto ainda carrega o escape de
   string JS (\") e json.loads() direto falha. Em vez de reimplementar
   regras de escape na mão, reaproveitamos o proprio decoder de string
   do json: embrulhamos o trecho em aspas e deixamos o json.loads
   desfazer o escape pra gente.
"""

import json
import logging
from datetime import datetime, timezone

from common.schema import MonitorAd

logger = logging.getLogger(__name__)


def _try_unescape(raw: str) -> str:
    if '\\"' not in raw:
        return raw
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def extract_ads(html: str) -> list[dict]:
    marker = None
    start = -1
    for candidato in ('\\"ads\\":[', '"ads":['):
        start = html.find(candidato)
        if start != -1:
            marker = candidato
            break
    if marker is None:
        raise ValueError("chave 'ads' não encontrada no HTML — a OLX pode ter mudado o front-end")

    abre = start + len(marker) - 1  # posição do '[' de abertura
    depth = 0
    in_string = False
    escape = False

    for j in range(abre, len(html)):
        ch = html[j]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                raw = html[abre : j + 1]
                return json.loads(_try_unescape(raw))

    raise ValueError("array 'ads' não fechou corretamente — HTML truncado ou mudou de formato")


def build_ads(raw_items: list[dict]) -> list[MonitorAd]:
    """Valida uma lista de dicts brutos já extraídos — de 1 página ou de
    N páginas concatenadas, não importa pra esta função.

    Importante: todos os anúncios de uma mesma chamada recebem o MESMO
    `coletado_em`, gerado uma única vez aqui. Se cada MonitorAd gerasse
    seu próprio timestamp (via default_factory, microssegundo a
    microssegundo), get_last_seen_ids() e as queries do dashboard —
    que agrupam por coletado_em pra saber 'o que veio nesta rodada' —
    quebrariam silenciosamente, pegando só o último anúncio da lista.
    """
    momento_coleta = datetime.now(timezone.utc)
    ads: list[MonitorAd] = []
    for item in raw_items:
        if "listId" not in item:
            continue  # placeholder de propaganda misturado no array, nao e anuncio
        try:
            ad = MonitorAd.from_olx_json(item)
            ad.coletado_em = momento_coleta
            ads.append(ad)
        except Exception as e:
            logger.warning("Falha ao parsear anúncio %s: %s", item.get("listId"), e)
    return ads


def parse_ads(html: str) -> list[MonitorAd]:
    """Mantido para compatibilidade com quem só tem uma página de HTML
    (e com os testes existentes, que continuam passando sem alteração)."""
    return build_ads(extract_ads(html))

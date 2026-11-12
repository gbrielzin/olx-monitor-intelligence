"""Camadas bronze e prata do lake.

- **Bronze**: o JSON bruto de cada coleta, do jeito que saiu do HTML da OLX
  (`extract_ads`), mais metadados (quando, de onde, quantas páginas). Nunca é
  alterado depois de gravado. Se o parser ou o schema tiverem bug, dá pra
  reprocessar a prata a partir daqui sem raspar a OLX de novo.
- **Prata**: anúncios limpos -- validados pelo mesmo schema Pydantic do
  scraper (`common/schema.py`), sem placeholder de propaganda, deduplicados
  por `listing_id` dentro da coleta, com a mesma régua de "preço confiável"
  que a mediana do sistema usa (`common.stats.margem_e_confiavel`). Uma linha
  por (anúncio, coleta), em JSON Lines.

Nome do vendedor (vem no bruto, em `olxPay`) fica só na bronze, que é privada
e nunca vai pro git (data/ está no .gitignore). A prata não carrega dado
pessoal: pra análise de preço ele não serve pra nada.

Chaves no estilo "partição Hive" (`uf=ES/data=2026-11-12/`): é o formato que
Athena/Spark/DuckDB entendem direto se um dia isso for pra nuvem.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from common.schema import ComputadorAd, IphoneAd, MonitorAd
from common.stats import margem_e_confiavel
from pipeline.armazenamento import Armazenamento

logger = logging.getLogger(__name__)

VERSAO_BRONZE = 1

CLASSES_POR_CATEGORIA = {"iphone": IphoneAd, "monitor": MonitorAd, "computador": ComputadorAd}


def _carimbo(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def chave_bronze(
    categoria: str, uf: str, coletado_em: datetime, plataforma: str = "olx", camada: str = "bronze"
) -> str:
    momento = coletado_em.astimezone(timezone.utc)
    return (
        f"{camada}/{plataforma}/{categoria}/uf={uf.upper()}/data={momento.date().isoformat()}/"
        f"coleta_{_carimbo(momento)}.json"
    )


def chave_prata_de(chave_bronze_: str) -> str:
    if not chave_bronze_.startswith("bronze/"):
        raise ValueError(f"não é chave de bronze: {chave_bronze_!r}")
    return "prata/" + chave_bronze_.removeprefix("bronze/").removesuffix(".json") + ".jsonl"


def gravar_bronze(
    arm: Armazenamento,
    raw_items: list[dict],
    *,
    categoria: str,
    uf: str,
    coletado_em: datetime,
    urls: list[str],
    plataforma: str = "olx",
    camada: str = "bronze",
) -> str:
    """Grava o bruto de uma coleta. `camada="quarentena"` é usado pelo flow
    quando o checkpoint de sanidade reprova a coleta: o dado não segue pra
    prata/ouro, mas fica guardado pra investigar o que a OLX mudou."""
    payload = {
        "metadados": {
            "versao": VERSAO_BRONZE,
            "plataforma": plataforma,
            "categoria": categoria,
            "uf": uf.upper(),
            "coletado_em": coletado_em.astimezone(timezone.utc).isoformat(),
            "urls": urls,
            "total_itens": len(raw_items),
            "total_com_listid": sum(1 for i in raw_items if "listId" in i),
        },
        "itens": raw_items,
    }
    chave = chave_bronze(categoria, uf, coletado_em, plataforma, camada)
    return arm.gravar(chave, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def ler_json(arm: Armazenamento, chave: str) -> dict:
    return json.loads(arm.ler(chave).decode("utf-8"))


def bronze_para_prata(payload: dict) -> list[dict]:
    """Transforma o bruto de UMA coleta em linhas limpas. Determinística: o
    mesmo bronze sempre gera a mesma prata (o `coleta_em` vem dos metadados,
    não do relógio), por isso dá pra reprocessar quantas vezes quiser."""
    meta = payload["metadados"]
    categoria = meta["categoria"]
    ad_class = CLASSES_POR_CATEGORIA[categoria]
    coleta_em = meta["coletado_em"]

    linhas: list[dict] = []
    vistos: set[int] = set()
    descartados = 0
    for item in payload["itens"]:
        if "listId" not in item:
            continue  # placeholder de propaganda
        try:
            ad = ad_class.from_olx_json(item)
        except Exception as e:  # mesmo critério de parser.build_ads
            descartados += 1
            logger.warning("prata: anúncio %s inválido no schema: %s", item.get("listId"), e)
            continue
        if ad.listing_id in vistos:
            continue  # paginação da OLX repete anúncio dentro da mesma rodada
        vistos.add(ad.listing_id)
        modelo = getattr(ad, "modelo", None)
        linhas.append({
            "coleta_em": coleta_em,
            "plataforma": ad.plataforma,
            "categoria": ad.categoria,
            "listing_id": ad.listing_id,
            "titulo": ad.titulo,
            "preco": ad.preco,
            "preco_antigo": ad.preco_antigo,
            "uf": ad.uf or meta["uf"],
            "municipio": ad.municipio,
            "condicao": ad.condicao,
            "modelo": modelo,
            "armazenamento_gb": getattr(ad, "armazenamento_gb", None),
            "grupo": ad.grupo,
            "data_publicacao": ad.data_publicacao.isoformat(),
            "url": ad.url,
            "confiavel": ad.preco is not None
            and margem_e_confiavel(ad.condicao, ad.titulo, ad.preco, ad.categoria, modelo),
        })
    if descartados:
        logger.warning("prata: %d anúncio(s) descartado(s) por falha de schema", descartados)
    return linhas


def gravar_prata(arm: Armazenamento, chave_bronze_: str, linhas: list[dict]) -> str:
    corpo = "\n".join(json.dumps(ln, ensure_ascii=False) for ln in linhas)
    return arm.gravar(chave_prata_de(chave_bronze_), corpo.encode("utf-8"))


def ler_prata(arm: Armazenamento, chave: str) -> list[dict]:
    texto = arm.ler(chave).decode("utf-8")
    return [json.loads(ln) for ln in texto.splitlines() if ln.strip()]

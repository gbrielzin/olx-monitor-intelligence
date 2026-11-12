"""Flow do Prefect: a mesma rodada do scraper, com orquestração e camadas.

    coletar -> sanidade -> diff -> salvar bronze -> transformar -> alertar

- **coletar**: varre as páginas da busca (`scraper/coleta.py`, o mesmo código
  do caminho antigo). É a única task com retry: falha de rede/HTTP (ex.: um
  403 isolado da Cloudflare) tenta de novo depois de 1 e de 5 minutos.
  Falha de parser (JSON de anúncios sumiu do HTML) NÃO tenta de novo: o HTML
  não vai mudar sozinho em 1 minuto, e insistir só aumenta a pegada.
- **sanidade**: valida pelo schema e passa pelo checkpoint de sempre
  (`scraper/sanity.py`). Reprovou -> o bruto vai pra `quarentena/` no lake
  (pra investigar depois) e a rodada para, sem gravar nada no banco.
- **diff**: `upsert_ads` no banco operacional (o mesmo que o dashboard lê) e
  separação de novos/quedas -- os gatilhos de alerta.
- **salvar bronze**: JSON bruto da coleta no lake.
- **transformar**: bronze -> prata (limpo, sem dado pessoal) -> ouro (fato/
  dimensão) + snapshot diário de mediana + CSVs da ouro no lake.
- **alertar**: mesmas mensagens e regras do caminho antigo (`scraper/alertas.py`).

Se qualquer task falhar de vez, o flow termina em estado Failed (visível na UI
local do Prefect) e o hook `_avisar_falha` manda aviso pro chat PESSOAL do
Telegram -- nunca pra grupo público. Sem token no .env, só loga.

Rodar uma vez (sem servidor, o Prefect sobe um temporário sozinho):

    PYTHONPATH=. python flows/pipeline_olx.py

Agendado a cada 12 min, com UI local em http://127.0.0.1:4200 (ver README):

    prefect server start          # terminal 1
    PYTHONPATH=. python flows/pipeline_olx.py --agendar   # terminal 2

Nada aqui substitui o caminho antigo: `docker-compose up` continua rodando
`scraper/main.py` com APScheduler, sem Prefect. Não rode os dois ao mesmo
tempo contra o mesmo banco -- cada um faria sua própria coleta e o checkpoint
de sanidade/sumiço veria rodadas em dobro.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
for _p in (_RAIZ, _RAIZ / "scraper"):  # scraper/ usa import "achatado" (ver tests/conftest.py)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from prefect import flow, get_run_logger, task  # noqa: E402
from prefect.cache_policies import NO_CACHE  # noqa: E402

from alertas import disparar_alertas  # noqa: E402
from coleta import coletar_paginas  # noqa: E402
from common.config import settings  # noqa: E402
from common.schema import IphoneAd  # noqa: E402
from common.stats import grava_snapshot_diario  # noqa: E402
from common.storage import contagem_media_ultimas_coletas, init_db, upsert_ads  # noqa: E402
from diff import separar_novidades  # noqa: E402
from fetcher import FetchError  # noqa: E402
from notifier import enviar_telegram  # noqa: E402
from parser import build_ads  # noqa: E402
from pipeline import ouro  # noqa: E402
from pipeline.armazenamento import Armazenamento, armazenamento_padrao  # noqa: E402
from pipeline.camadas import bronze_para_prata, gravar_bronze, gravar_prata, ler_json  # noqa: E402
from sanity import checar_sanidade  # noqa: E402


class SanidadeReprovada(Exception):
    pass


def _so_falha_de_rede(task, task_run, state) -> bool:
    """retry_condition_fn: tenta de novo só quando o erro é de rede/HTTP."""
    try:
        state.result()
    except FetchError:
        return True
    except Exception:
        return False
    return False


@task(retries=2, retry_delay_seconds=[60, 300], retry_condition_fn=_so_falha_de_rede, cache_policy=NO_CACHE)
def coletar(search_url: str, max_paginas: int) -> tuple[list[dict], list[str]]:
    log = get_run_logger()
    raw_items, urls = coletar_paginas(search_url, max_paginas)
    log.info("coletar: %d itens brutos em %d página(s)", len(raw_items), len(urls))
    return raw_items, urls


@task(cache_policy=NO_CACHE)
def sanidade(raw_items: list[dict], urls: list[str], *, ad_class, categoria: str, uf: str, arm: Armazenamento) -> list:
    log = get_run_logger()
    ads = build_ads(raw_items, ad_class)
    resultado = checar_sanidade(ads, contagem_media_ultimas_coletas(categoria=categoria, uf=uf))
    if not resultado.ok:
        chave = gravar_bronze(
            arm, raw_items, categoria=categoria, uf=uf, coletado_em=datetime.now(timezone.utc),
            urls=urls, camada="quarentena",
        )
        log.error("sanidade reprovou (%s): %s -- bruto guardado em %s", uf, resultado.motivo, chave)
        raise SanidadeReprovada(resultado.motivo)
    log.info("sanidade ok: %d anúncios válidos", len(ads))
    return ads


@task(cache_policy=NO_CACHE)
def diff(ads: list, *, uf: str):
    log = get_run_logger()
    resultado = upsert_ads(ads, uf=uf)
    novos, quedas = separar_novidades(ads, resultado)
    log.info("diff: %d novos, %d com queda de preço", len(novos), len(quedas))
    return novos, quedas, resultado


@task(cache_policy=NO_CACHE)
def salvar_bronze(raw_items: list[dict], urls: list[str], *, coletado_em, categoria: str, uf: str, arm: Armazenamento) -> str:
    chave = gravar_bronze(arm, raw_items, categoria=categoria, uf=uf, coletado_em=coletado_em, urls=urls)
    get_run_logger().info("bronze: %s", chave)
    return chave


@task(cache_policy=NO_CACHE)
def transformar(chave_bronze: str, *, categoria: str, arm: Armazenamento, ouro_db_path: str) -> dict:
    log = get_run_logger()
    linhas = bronze_para_prata(ler_json(arm, chave_bronze))
    chave_prata = gravar_prata(arm, chave_bronze, linhas)
    with ouro.conectar(ouro_db_path) as conn:
        no_fato = ouro.carregar_prata(conn, linhas)
        ouro.publicar_csv(arm, conn)
    snapshot = grava_snapshot_diario(categoria)
    log.info("prata: %d linhas (%s) | ouro: +%d no fato | medianas_diarias: +%d", len(linhas), chave_prata, no_fato, snapshot)
    return {"prata": len(linhas), "ouro_novas": no_fato, "medianas_diarias": snapshot}


@task(cache_policy=NO_CACHE)
def alertar(novos: list, quedas: list, resultado, *, nome: str, chat_id: str | None) -> int:
    enviados = disparar_alertas(novos, quedas, resultado, nome=nome, chat_id_oportunidade=chat_id)
    get_run_logger().info("alertar: %d alerta(s) de oportunidade enviados", enviados)
    return enviados


def _avisar_falha(flow, flow_run, state) -> None:
    enviar_telegram(f"⚠️ Pipeline Prefect ({flow_run.name}) falhou: {state.message}")


@flow(name="pipeline-olx-iphone", on_failure=[_avisar_falha], log_prints=True)
def pipeline_olx(max_paginas: int | None = None, ufs: list[str] | None = None) -> dict:
    """Uma rodada por região configurada (IPHONE_REGIOES, padrão só ES).
    `ufs` filtra as regiões (ex.: ["ES"]); `max_paginas` sobrescreve o .env
    (útil pra uma rodada de teste curta)."""
    init_db()
    arm = armazenamento_padrao()
    paginas = max_paginas or settings.max_paginas
    regioes = [r for r in settings.iphone_regioes if not ufs or r.uf.upper() in {u.upper() for u in ufs}]
    resumo = {}
    for i, regiao in enumerate(regioes):
        if i > 0:
            time.sleep(settings.intervalo_entre_regioes_segundos)
        nome = f"iPhone ({regiao.nome})"
        url = settings.iphone_search_url_template.format(uf=regiao.uf.lower())
        raw_items, urls = coletar(url, paginas)
        ads = sanidade(raw_items, urls, ad_class=IphoneAd, categoria="iphone", uf=regiao.uf, arm=arm)
        novos, quedas, resultado = diff(ads, uf=regiao.uf)
        chave = salvar_bronze(raw_items, urls, coletado_em=ads[0].coletado_em, categoria="iphone", uf=regiao.uf, arm=arm)
        contagens = transformar(chave, categoria="iphone", arm=arm, ouro_db_path=settings.ouro_db_path)
        enviados = alertar(novos, quedas, resultado, nome=nome, chat_id=regiao.chat_id)
        resumo[regiao.uf] = {
            "anuncios": len(ads), "novos": len(novos), "quedas": len(quedas),
            "bronze": chave, **contagens, "alertas": enviados,
        }
    return resumo


if __name__ == "__main__":
    if "--agendar" in sys.argv:
        # Precisa de um servidor Prefect local rodando (`prefect server start`)
        # e PREFECT_API_URL apontando pra ele -- ver README.
        pipeline_olx.serve(name="olx-iphone-12min", interval=settings.scrape_interval_minutes * 60)
    else:
        print(pipeline_olx())

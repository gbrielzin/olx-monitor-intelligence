"""Flow do Prefect de ponta a ponta, sem rede: o HTML da OLX é falso, o lake
é uma pasta temporária e o Telegram é interceptado. Pulado se o Prefect não
estiver instalado (requirements-pipeline.txt)."""

import json

import pytest

pytest.importorskip("prefect")

from prefect.testing.utilities import prefect_test_harness  # noqa: E402

from common.config import Regiao, settings  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def servidor_prefect_temporario():
    with prefect_test_harness():
        yield


def _html(itens):
    corpo = json.dumps(itens).replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"x \\"ads\\":{corpo} y"])</script>'


def _item(listing_id, preco):
    return {
        "listId": listing_id, "subject": "iPhone 13 128GB", "priceValue": f"R$ {preco}",
        "url": f"https://es.olx.com.br/x-{listing_id}", "date": 1787000000,
        "locationDetails": {"municipality": "Vitória", "uf": "ES"},
        "properties": [
            {"name": "electronics_model", "value": "IPHONE 13"},
            {"name": "cellphone_storage", "value": "128GB"},
            {"name": "electronics_condition", "value": "Usado - Excelente"},
        ],
        "olxPay": {"transactionalSellerName": "Fulano"},
    }


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    import alertas
    import coleta

    import flows.pipeline_olx as fluxo

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "op.db"))
    monkeypatch.setattr(settings, "lake_backend", "local")
    monkeypatch.setattr(settings, "lake_dir", str(tmp_path / "lake"))
    monkeypatch.setattr(settings, "ouro_db_path", str(tmp_path / "ouro.db"))
    monkeypatch.setattr(settings, "iphone_regioes", [Regiao(uf="ES", nome="Espírito Santo", chat_id="pessoal")])

    telegram = []
    monkeypatch.setattr(alertas, "enviar_telegram", lambda m, chat_id=None: telegram.append(m))
    monkeypatch.setattr(fluxo, "enviar_telegram", lambda m, chat_id=None: telegram.append(m))
    monkeypatch.setattr(alertas, "auditar_anuncio", lambda ad: None)
    # sem espera de verdade entre tentativas nos testes
    monkeypatch.setattr(fluxo, "coletar", fluxo.coletar.with_options(retry_delay_seconds=0))

    respostas = []
    pedidas = []

    def fetch_falso(url):
        pedidas.append(url)
        r = respostas.pop(0) if len(respostas) > 1 else respostas[0]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(coleta, "fetch_html", fetch_falso)
    return {"fluxo": fluxo, "respostas": respostas, "pedidas": pedidas, "telegram": telegram, "tmp": tmp_path}


def test_rodada_completa_grava_banco_e_as_tres_camadas(ambiente):
    import sqlite3

    ambiente["respostas"].append(_html([_item(i, 2000 + i) for i in range(1, 8)] + [{"advertisingId": "x"}]))
    resumo = ambiente["fluxo"].pipeline_olx(max_paginas=1)

    es = resumo["ES"]
    assert (es["anuncios"], es["novos"], es["quedas"], es["prata"], es["ouro_novas"]) == (7, 7, 0, 7, 7)

    lake = ambiente["tmp"] / "lake"
    assert len(list((lake / "bronze").rglob("*.json"))) == 1
    assert len(list((lake / "prata").rglob("*.jsonl"))) == 1
    assert {p.name for p in (lake / "ouro").iterdir()} == {
        "dim_data.csv", "dim_modelo.csv", "dim_local.csv", "fato_anuncio_snapshot.csv"
    }
    # banco operacional (o do dashboard) continua sendo alimentado
    op = sqlite3.connect(ambiente["tmp"] / "op.db")
    assert op.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 7
    # bronze e banco operacional usam o mesmo carimbo de coleta
    coletado_em = op.execute("SELECT coletado_em FROM coletas").fetchone()[0]
    assert coletado_em[:10] in es["bronze"]


def test_sanidade_reprovada_vai_pra_quarentena_e_nao_grava(ambiente):
    import sqlite3

    ambiente["respostas"].append(_html([{"advertisingId": "so-propaganda"}]))  # zero anúncios
    estado = ambiente["fluxo"].pipeline_olx(max_paginas=1, return_state=True)

    assert estado.is_failed()
    lake = ambiente["tmp"] / "lake"
    assert len(list((lake / "quarentena").rglob("*.json"))) == 1
    assert not (lake / "bronze").exists()
    op = sqlite3.connect(ambiente["tmp"] / "op.db")
    assert op.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 0
    assert any("Pipeline Prefect" in m for m in ambiente["telegram"])


def test_falha_de_rede_tenta_de_novo(ambiente):
    from fetcher import FetchError

    ambiente["respostas"].extend([FetchError("403 Forbidden"), _html([_item(1, 2000)])])
    resumo = ambiente["fluxo"].pipeline_olx(max_paginas=1)
    assert len(ambiente["pedidas"]) == 2
    assert resumo["ES"]["anuncios"] == 1


def test_parser_quebrado_nao_tenta_de_novo(ambiente):
    ambiente["respostas"].append("<html>front-end novo, sem ads</html>")
    estado = ambiente["fluxo"].pipeline_olx(max_paginas=1, return_state=True)
    assert estado.is_failed()
    assert len(ambiente["pedidas"]) == 1

"""scraper/coleta.py e scraper/alertas.py saíram de dentro de main.py pra o
flow do Prefect reaproveitar. Estes testes travam que o comportamento do
caminho antigo continua o mesmo -- e o bug do aviso de parser quebrado."""

import json

import pytest

from scraper import coleta


def _html(n, inicio=1):
    ads = [{"listId": i, "subject": f"iPhone {i}", "url": f"https://x/{i}", "date": 1787000000}
           for i in range(inicio, inicio + n)]
    corpo = json.dumps(ads).replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"x \\"ads\\":{corpo} y"])</script>'


def test_coletar_paginas_para_na_pagina_incompleta(monkeypatch):
    paginas = {1: _html(50, 1), 2: _html(50, 51), 3: _html(7, 101)}
    pedidas = []

    def falso(url):
        pedidas.append(url)
        n = int(url.split("o=")[1]) if "o=" in url else 1
        return paginas[n]

    monkeypatch.setattr(coleta, "fetch_html", falso)
    pausas = []
    raw, urls = coleta.coletar_paginas("https://olx/busca?q=iphone", 10, pausa=pausas.append)

    assert len([i for i in raw if "listId" in i]) == 107
    assert urls == pedidas == ["https://olx/busca?q=iphone", "https://olx/busca?q=iphone&o=2", "https://olx/busca?q=iphone&o=3"]
    assert pausas == [1, 1]  # pausa só entre páginas


def test_coletar_paginas_respeita_teto(monkeypatch):
    monkeypatch.setattr(coleta, "fetch_html", lambda url: _html(50))
    _, urls = coleta.coletar_paginas("https://olx/b", 2, pausa=lambda s: None)
    assert len(urls) == 2


def test_rodar_coleta_avisa_quando_o_json_de_anuncios_some(monkeypatch):
    """Regressão: o ValueError do extract_ads escapava da rodada sem aviso no
    Telegram (o except estava em volta de build_ads, que não levanta isso)."""
    from scraper import main

    avisos = []
    monkeypatch.setattr(main, "coletar_paginas", lambda *a, **k: (_ for _ in ()).throw(ValueError("chave 'ads' não encontrada")))
    monkeypatch.setattr(main, "enviar_telegram", lambda msg, chat_id=None: avisos.append(msg))

    main.rodar_coleta(nome="iPhone (ES)", search_url="u", ad_class=None, categoria="iphone", uf="ES")

    assert len(avisos) == 1
    assert "possivelmente quebrado" in avisos[0]


def test_rodar_coleta_avisa_falha_de_rede(monkeypatch):
    from scraper import main
    from fetcher import FetchError

    avisos = []
    monkeypatch.setattr(main, "coletar_paginas", lambda *a, **k: (_ for _ in ()).throw(FetchError("Read timed out")))
    monkeypatch.setattr(main, "enviar_telegram", lambda msg, chat_id=None: avisos.append(msg))

    main.rodar_coleta(nome="iPhone (ES)", search_url="u", ad_class=None, categoria="iphone", uf="ES")
    assert len(avisos) == 1 and "falha ao buscar" in avisos[0]


@pytest.fixture
def banco(tmp_path, monkeypatch):
    from common.config import settings
    from common import storage

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "op.db"))
    storage.init_db()
    return storage


def _iphone(listing_id, preco):
    from datetime import datetime, timezone

    from common.schema import IphoneAd

    return IphoneAd.model_validate({
        "listing_id": listing_id, "titulo": "iPhone 13 128GB", "url": f"https://x/{listing_id}",
        "data_publicacao": "2026-01-01T00:00:00", "preco": preco, "modelo": "IPHONE 13",
        "armazenamento_gb": 128, "uf": "ES", "condicao": "Usado - Excelente",
        "coletado_em": datetime(2026, 11, 12, tzinfo=timezone.utc),
    })


def test_disparar_alertas_manda_so_oportunidade_e_conta(banco, monkeypatch):
    from scraper import alertas
    from common.storage import ColetaResultado

    # 6 anúncios a R$ 1.000 no grupo -> mediana 1.000; um novo a R$ 700 (70%) é oportunidade.
    banco.upsert_ads([_iphone(i, 1000) for i in range(1, 7)] + [_iphone(99, 700)], uf="ES")
    enviados = []
    monkeypatch.setattr(alertas, "enviar_telegram", lambda msg, chat_id=None: enviados.append((msg, chat_id)))
    monkeypatch.setattr(alertas, "auditar_anuncio", lambda ad: None)

    n = alertas.disparar_alertas(
        [_iphone(99, 700), _iphone(1, 1000)], [], ColetaResultado(novos={99, 1}),
        nome="iPhone (ES)", chat_id_oportunidade="grupo-es",
    )
    assert n == 1
    assert enviados[0][1] == "grupo-es"
    assert "Oportunidade (novo anúncio)" in enviados[0][0]

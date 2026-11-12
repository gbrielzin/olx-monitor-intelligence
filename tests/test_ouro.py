"""Camada ouro: carga do modelo estrela e as consultas com window function.
Os números esperados são calculados à mão a partir das linhas montadas em
cada teste -- não vêm do banco real."""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline import consultas_ouro, ouro
from pipeline.armazenamento import ArmazenamentoLocal


def _linha(listing_id, coleta_em, preco, modelo="IPHONE 13", gb=128, uf="ES", municipio="Vitória", confiavel=True):
    return {
        "coleta_em": coleta_em.isoformat(), "plataforma": "olx", "categoria": "iphone",
        "listing_id": listing_id, "titulo": "x", "preco": preco, "preco_antigo": None,
        "uf": uf, "municipio": municipio, "condicao": "Usado - Bom", "modelo": modelo,
        "armazenamento_gb": gb, "grupo": "g", "data_publicacao": "2026-01-01T00:00:00",
        "url": f"https://x/{listing_id}", "confiavel": confiavel,
    }


def _t(dia, hora=15):
    return datetime(2026, 11, dia, hora, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    with ouro.conectar(":memory:") as c:
        yield c


def test_carga_cria_dimensoes_e_fato(conn):
    n = ouro.carregar_prata(conn, [_linha(1, _t(10), 2000), _linha(2, _t(10), 2100, municipio="Serra")])
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM dim_modelo").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dim_local").fetchone()[0] == 2
    assert conn.execute("SELECT data_key, dia_semana, fim_de_semana FROM dim_data").fetchall() == [(20261110, 1, 0)]


def test_carga_e_idempotente(conn):
    linhas = [_linha(1, _t(10), 2000), _linha(1, _t(11), 1900)]
    assert ouro.carregar_prata(conn, linhas) == 2
    assert ouro.carregar_prata(conn, linhas) == 0
    assert conn.execute("SELECT COUNT(*) FROM fato_anuncio_snapshot").fetchone()[0] == 2


def test_linha_sem_preco_nao_entra_no_fato(conn):
    assert ouro.carregar_prata(conn, [_linha(1, _t(10), None)]) == 0


def test_modelo_e_local_desconhecidos_viram_interrogacao(conn):
    ouro.carregar_prata(conn, [_linha(1, _t(10), 2000, modelo=None, gb=None, municipio=None)])
    assert conn.execute("SELECT chave_natural, armazenamento_gb FROM dim_modelo").fetchone() == ("iphone|?|?", None)
    assert conn.execute("SELECT chave_natural FROM dim_local").fetchone() == ("ES|?",)


def test_data_do_snapshot_e_o_dia_de_brasilia(conn):
    # 01h UTC do dia 11 = 22h do dia 10 em Brasília
    ouro.carregar_prata(conn, [_linha(1, datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc), 2000)])
    assert conn.execute("SELECT data_key FROM fato_anuncio_snapshot").fetchone()[0] == 20261110


def test_chave_estrangeira_e_cobrada(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fato_anuncio_snapshot (coleta_em, plataforma, listing_id, data_key, modelo_key, "
            "local_key, preco, confiavel) VALUES ('x', 'olx', 1, 99999999, 999, 999, 1, 1)"
        )


def test_reconstruir_do_lake_e_publicar_csv(tmp_path, conn):
    from pipeline.camadas import gravar_prata

    arm = ArmazenamentoLocal(tmp_path)
    gravar_prata(arm, "bronze/olx/iphone/uf=ES/data=2026-11-10/coleta_a.json", [_linha(1, _t(10), 2000)])
    gravar_prata(arm, "bronze/olx/iphone/uf=ES/data=2026-11-11/coleta_b.json", [_linha(1, _t(11), 1900)])
    assert ouro.reconstruir_do_lake(arm, conn) == 2
    assert ouro.reconstruir_do_lake(arm, conn) == 0

    chaves = ouro.publicar_csv(arm, conn)
    assert chaves == [f"ouro/{t}.csv" for t in ouro.TABELAS_OURO]
    fato = arm.ler("ouro/fato_anuncio_snapshot.csv").decode("utf-8-sig").splitlines()
    assert fato[0].startswith("coleta_em;plataforma;listing_id")
    assert len(fato) == 3  # cabeçalho + 2 linhas


# --- consultas ---

def test_lag_acha_queda_de_preco_entre_coletas(conn):
    ouro.carregar_prata(conn, [
        _linha(1, _t(10), 2000), _linha(1, _t(11), 1800), _linha(1, _t(12), 1900),  # caiu 10%, depois subiu
        _linha(2, _t(10), 3000), _linha(2, _t(11), 3000),  # parado
    ])
    quedas = consultas_ouro.rodar(conn, "quedas_de_preco")
    assert len(quedas) == 1
    assert quedas[0]["listing_id"] == 1
    assert quedas[0]["preco_anterior"] == 2000
    assert quedas[0]["preco"] == 1800
    assert quedas[0]["variacao_pct"] == -0.1


def test_row_number_ranqueia_mais_baratos_na_ultima_coleta(conn):
    ouro.carregar_prata(conn, [
        _linha(9, _t(10), 100),  # coleta antiga: não conta
        _linha(1, _t(11), 2000), _linha(2, _t(11), 1500), _linha(3, _t(11), 1800), _linha(4, _t(11), 2500),
        _linha(5, _t(11), 900, confiavel=False),  # suspeito: fora do ranking
        _linha(6, _t(11), 4000, modelo="IPHONE 14"),
    ])
    linhas = consultas_ouro.rodar(conn, "mais_baratos_por_modelo", top=3)
    iphone13 = [(ln["posicao"], ln["listing_id"]) for ln in linhas if ln["modelo"] == "IPHONE 13"]
    assert iphone13 == [(1, 2), (2, 3), (3, 1)]
    assert {ln["anuncios_no_modelo"] for ln in linhas if ln["modelo"] == "IPHONE 13"} == {4}
    assert [ln["listing_id"] for ln in linhas if ln["modelo"] == "IPHONE 14"] == [6]


def test_mediana_diaria_usa_um_preco_por_anuncio_por_dia(conn):
    # Anúncio 1 aparece em 3 coletas no mesmo dia: conta 1 vez, com o último preço (1000).
    linhas = [
        _linha(1, _t(10, 12), 5000), _linha(1, _t(10, 13), 5000), _linha(1, _t(10, 14), 1000),
        _linha(2, _t(10), 2000), _linha(3, _t(10), 3000), _linha(4, _t(10), 4000),
    ]
    ouro.carregar_prata(conn, linhas)
    r = consultas_ouro.rodar(conn, "mediana_movel_7d", amostra_minima=1)
    assert len(r) == 1
    assert r[0]["amostra"] == 4
    assert r[0]["mediana_dia"] == 2500  # mediana de [1000, 2000, 3000, 4000] (n par: média dos 2 do meio)


def test_media_movel_usa_7_dias_corridos_mesmo_com_buraco_de_coleta(conn):
    # Dias 1, 2 e 10: no dia 10 a janela (4 a 10) só tem ele mesmo -- os dias 1 e 2
    # ficaram mais de 6 dias pra trás. Com ROWS (7 linhas) a janela pegaria os três.
    linhas = []
    for dia, precos in ((1, (1000, 1100, 1200)), (2, (2000, 2100, 2200)), (10, (3000, 3100, 3200))):
        for i, p in enumerate(precos):
            linhas.append(_linha(dia * 10 + i, _t(dia), p))
    ouro.carregar_prata(conn, linhas)
    r = consultas_ouro.rodar(conn, "mediana_movel_7d", amostra_minima=1)
    assert [(x["data"], x["mediana_dia"], x["media_movel_7d"], x["dias_com_dado_na_janela"]) for x in r] == [
        ("2026-11-01", 1100, 1100, 1),
        ("2026-11-02", 2100, 1600, 2),
        ("2026-11-10", 3100, 3100, 1),
    ]


def test_mediana_movel_respeita_amostra_minima(conn):
    ouro.carregar_prata(conn, [_linha(i, _t(10), 1000 + i) for i in range(3)])
    assert consultas_ouro.rodar(conn, "mediana_movel_7d") == []  # padrão exige 5 anúncios no dia
    assert len(consultas_ouro.rodar(conn, "mediana_movel_7d", amostra_minima=3)) == 1


def test_indices_do_fato_existem(conn):
    nomes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_fato_modelo_data", "idx_fato_local", "idx_fato_coleta"} <= nomes


def test_mediana_impar(conn):
    ouro.carregar_prata(conn, [_linha(i, _t(10) + timedelta(minutes=i), p) for i, p in enumerate((5, 1, 3))])
    # preços abaixo do piso não importam aqui: confiavel vem pronto da prata
    assert consultas_ouro.rodar(conn, "mediana_movel_7d", amostra_minima=1)[0]["mediana_dia"] == 3

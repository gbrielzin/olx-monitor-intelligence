import pandas as pd

from common.insights import (
    cobertura_coleta,
    com_razao_mediana,
    conflitos_titulo_modelo,
    dispersao_por_categoria,
    resumo_quedas,
    tempo_no_ar_por_faixa,
)


def _ad(preco, grupo="G1", categoria="iphone", ativo=1, dias=None, titulo="iPhone 13", modelo="IPHONE 13",
        condicao="Usado - Bom", polegadas=None):
    inicio = pd.Timestamp("2026-09-01", tz="UTC")
    return {
        "categoria": categoria, "grupo": grupo, "modelo": modelo, "titulo": titulo, "preco": preco,
        "condicao": condicao, "polegadas": polegadas, "ativo": ativo,
        "primeiro_visto_em": inicio.isoformat(),
        "removido_em": (inicio + pd.Timedelta(days=dias)).isoformat() if dias is not None else None,
        "url": f"https://x/{preco}-{dias}",
    }


def _base_grupo(precos=(1000, 1000, 1000, 1000, 1000, 1000)):
    return [_ad(p) for p in precos]


def test_razao_ignora_grupo_com_amostra_pequena():
    df = pd.DataFrame([_ad(1000), _ad(1200)])  # 2 < amostra mínima
    assert com_razao_mediana(df)["razao"].isna().all()


def test_razao_exclui_anuncio_com_conflito_titulo_modelo():
    df = pd.DataFrame(_base_grupo() + [_ad(500, titulo="iPhone 17 Pro", modelo="IPHONE 13")])
    out = com_razao_mediana(df)
    assert out.iloc[-1]["razao"] != out.iloc[-1]["razao"]  # NaN
    assert (out.iloc[:-1]["razao"] == 1.0).all()  # e não contaminou a mediana


def test_tempo_no_ar_por_faixa_separa_abaixo_do_limiar():
    barato = [_ad(700, ativo=0, dias=1) for _ in range(3)]
    normal = [_ad(1000, ativo=0, dias=4) for _ in range(3)]
    out = tempo_no_ar_por_faixa(pd.DataFrame(_base_grupo() + barato + normal), "iphone").set_index("faixa")
    assert out.loc["≤75%", "mediana_dias"] == 1.0
    assert out.loc["90–110%", "mediana_dias"] == 4.0
    assert out.loc["≤75%", "anuncios"] == 3


def test_tempo_no_ar_sem_removidos_devolve_vazio():
    out = tempo_no_ar_por_faixa(pd.DataFrame(_base_grupo()), "iphone")
    assert out.empty


def test_resumo_quedas_ignora_alta_e_preco_anterior_zero():
    q = pd.DataFrame([
        {"preco": 90.0, "preco_anterior": 100.0, "url": "a"},
        {"preco": 80.0, "preco_anterior": 100.0, "url": "b"},
        {"preco": 120.0, "preco_anterior": 100.0, "url": "c"},  # subiu -- não é queda
        {"preco": 0.0, "preco_anterior": 0.0, "url": "d"},
    ])
    r = resumo_quedas(q, total_anuncios=10)
    assert r["quedas"] == 2 and r["anuncios_com_queda"] == 2
    assert r["mediana_pct"] == 15.0 and r["pct_anuncios"] == 20.0


def test_resumo_quedas_vazio():
    assert resumo_quedas(pd.DataFrame(), 10)["mediana_pct"] is None


def test_dispersao_por_categoria_calcula_pct_abaixo_do_limiar():
    df = pd.DataFrame(_base_grupo((1000, 1000, 1000, 1000, 1000, 600)))
    linha = dispersao_por_categoria(df).iloc[0]
    assert linha["categoria"] == "iphone" and linha["anuncios"] == 6
    assert round(linha["pct_abaixo_limiar"], 1) == round(1 / 6 * 100, 1)


def test_conflitos_titulo_modelo_lista_so_os_conflitantes():
    df = pd.DataFrame([_ad(1000), _ad(3400, titulo="iPhone 17 Pro", modelo="IPHONE 13 PRO MAX")])
    assert len(conflitos_titulo_modelo(df)) == 1


def test_cobertura_coleta_conta_dias_distintos():
    c = pd.DataFrame({"coletado_em": [
        "2026-09-01T10:00:00+00:00", "2026-09-01T12:00:00+00:00", "2026-09-04T10:00:00+00:00",
    ]})
    assert cobertura_coleta(c) == {"rodadas": 3, "dias_com_coleta": 2, "dias_janela": 4}
    assert cobertura_coleta(pd.DataFrame())["rodadas"] == 0

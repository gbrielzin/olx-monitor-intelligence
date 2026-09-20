import pandas as pd

from dashboard.charts import grafico_distribuicao_preco, grafico_tendencia_quedas


def _linha(preco_anterior, preco, listing_id=1):
    return {
        "registrado_em": "2026-08-27T12:00:00",
        "preco": preco,
        "preco_anterior": preco_anterior,
        "titulo": f"anuncio {listing_id}",
        "categoria": "monitor",
        "marca": "AOC",
        "tipo_monitor": "Monitor Gamer",
        "condicao": "Usado - Bom",
        "municipio": "Vitoria",
        "url": f"https://x/{listing_id}",
    }


def test_grafico_tendencia_quedas_vazio_retorna_none():
    assert grafico_tendencia_quedas(pd.DataFrame()) is None


def test_grafico_tendencia_quedas_ignora_preco_anterior_zero_sem_quebrar():
    """Achado ao vivo em 2026-08-27: um anúncio 'doação' (R$0) reaparecendo
    gerava preco=0 E preco_anterior=0 na mesma linha -- queda_pct = 0/0 =
    NaN, e o plotly quebrava a página inteira do dashboard ao tentar usar
    NaN como tamanho do marcador. R$0 não tem '% de queda' que faça
    sentido, então a linha é ignorada, não gera exceção."""
    df = pd.DataFrame([_linha(preco_anterior=0.0, preco=0.0, listing_id=1)])
    assert grafico_tendencia_quedas(df) is None


def test_grafico_tendencia_quedas_mistura_zero_com_queda_real():
    df = pd.DataFrame(
        [
            _linha(preco_anterior=0.0, preco=0.0, listing_id=1),
            _linha(preco_anterior=1000.0, preco=800.0, listing_id=2),
        ]
    )
    fig = grafico_tendencia_quedas(df)
    assert fig is not None
    assert len(fig.data[0].x) == 1  # só a queda real de verdade entra no gráfico


def test_grafico_tendencia_quedas_ignora_reaparicao_com_preco_maior_sem_quebrar():
    """Achado ao vivo em 2026-09-07: um anúncio que sumiu e reaparece com
    preço MAIOR do que tinha antes de sumir é gravado em historico_precos
    como 'novo avistamento' com o preco_anterior de antes (ver
    storage.py:upsert_ads) -- não é uma queda de verdade, e o plotly
    quebrava a página inteira ao tentar usar tamanho de marcador negativo."""
    df = pd.DataFrame(
        [
            _linha(preco_anterior=500.0, preco=700.0, listing_id=1),  # reapareceu mais caro
            _linha(preco_anterior=1000.0, preco=800.0, listing_id=2),  # queda real
        ]
    )
    fig = grafico_tendencia_quedas(df)
    assert fig is not None
    assert len(fig.data[0].x) == 1  # só a queda real entra no gráfico


def test_grafico_tendencia_quedas_caso_normal_gera_figura():
    df = pd.DataFrame(
        [
            _linha(preco_anterior=1000.0, preco=800.0, listing_id=1),
            _linha(preco_anterior=500.0, preco=400.0, listing_id=2),
        ]
    )
    fig = grafico_tendencia_quedas(df)
    assert fig is not None
    assert len(fig.data[0].x) == 2


def test_grafico_distribuicao_preco_vazio_retorna_none():
    assert grafico_distribuicao_preco(pd.DataFrame({"preco": []})) is None


def test_grafico_distribuicao_preco_ignora_linha_sem_preco():
    df = pd.DataFrame({"preco": [100.0, None, 300.0]})
    fig = grafico_distribuicao_preco(df)
    assert fig is not None
    assert len(fig.data[0].x) == 2


def test_grafico_distribuicao_preco_marca_mediana_e_media_como_vlines():
    df = pd.DataFrame({"preco": [100.0, 200.0, 900.0]})  # média (400) != mediana (200)
    fig = grafico_distribuicao_preco(df)
    xs_vlines = sorted(shape["x0"] for shape in fig.layout.shapes)
    assert xs_vlines == [200.0, 400.0]

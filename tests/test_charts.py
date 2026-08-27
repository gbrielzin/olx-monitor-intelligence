import pandas as pd

from dashboard.charts import grafico_tendencia_quedas


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

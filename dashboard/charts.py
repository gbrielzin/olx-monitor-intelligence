import pandas as pd
import plotly.express as px


def grafico_dispersao_hz(df: pd.DataFrame, tipo_monitor: str):
    sub = df[
        (df["tipo_monitor"] == tipo_monitor) & df["hz_exato"].notna() & df["preco"].notna()
    ].copy()
    if sub.empty:
        return None

    mediana = sub["preco"].median()
    sub["desvio"] = (sub["preco"] - mediana) / mediana

    fig = px.scatter(
        sub,
        x="hz_exato",
        y="preco",
        color="desvio",
        color_continuous_scale=["green", "lightgray", "red"],
        range_color=[-0.4, 0.4],
        hover_data=["titulo", "marca", "municipio", "url"],
        labels={"hz_exato": "Taxa de atualização (Hz)", "preco": "Preço (R$)"},
        title=f"{tipo_monitor} — preço x Hz",
    )
    return fig


def grafico_distribuicao_preco(df: pd.DataFrame, titulo_sufixo: str = ""):
    """Histograma de preço com mediana e média marcadas -- explica
    visualmente por que common/stats.py usa mediana (não média) como preço
    de referência: um punhado de anúncio muito caro ou muito barato puxa a
    média, mas quase não move a mediana. Também é a primeira coisa que
    expõe visualmente um grupo com dado congelado (ver alerta de categoria
    descontinuada): a "margem" gigante nesses casos parte do MESMO
    fenômeno que este gráfico ilustra, só que sem outlier nenhum de
    verdade -- é média/mediana calculada sobre anúncio que não é mais
    checado."""
    sub = df[df["preco"].notna()]
    if sub.empty:
        return None
    mediana = sub["preco"].median()
    media = sub["preco"].mean()
    fig = px.histogram(
        sub,
        x="preco",
        nbins=40,
        labels={"preco": "Preço (R$)"},
        title=f"Distribuição de preço{titulo_sufixo}",
    )
    fig.add_vline(x=mediana, line_dash="dash", line_color="green")
    fig.add_annotation(x=mediana, y=1, yref="paper", yanchor="bottom", showarrow=False,
                        text=f"mediana R$ {mediana:.0f}", font=dict(color="green"))
    fig.add_vline(x=media, line_dash="dot", line_color="red")
    fig.add_annotation(x=media, y=0.92, yref="paper", yanchor="bottom", showarrow=False,
                        text=f"média R$ {media:.0f}", font=dict(color="red"))
    return fig


def grafico_tendencia_quedas(df: pd.DataFrame):
    """Quedas de preço reais ao longo do tempo -- só existe porque o schema
    novo grava 1 linha por evento de queda, não 1 linha por rodada de
    coleta. Fica esparso enquanto pouco histórico acumulou; enche sozinho."""
    if df.empty:
        return None
    sub = df.copy()
    # preco_anterior=0 é real (anúncio "doação"/grátis reaparecendo) mas não
    # tem % de queda que faça sentido (divisão por zero -> NaN -> plotly
    # quebra o marker size). Visto ao vivo em 2026-08-27.
    # Um anúncio que sumiu e reapareceu é gravado em historico_precos como
    # "novo avistamento" com o preco_anterior de antes de sumir (ver
    # storage.py:upsert_ads) -- se o preço voltou MAIOR do que era, isso não
    # é uma queda de verdade, e o Plotly quebra com tamanho de marker
    # negativo. Visto ao vivo em 2026-09-07.
    sub = sub[(sub["preco_anterior"] > 0) & (sub["preco"] < sub["preco_anterior"])]
    if sub.empty:
        return None
    sub["queda_pct"] = (sub["preco_anterior"] - sub["preco"]) / sub["preco_anterior"] * 100

    fig = px.scatter(
        sub,
        x="registrado_em",
        y="queda_pct",
        color="marca",
        size="queda_pct",
        hover_data=["titulo", "preco_anterior", "preco", "condicao", "url"],
        labels={"registrado_em": "Quando", "queda_pct": "Queda (%)"},
        title="Quedas de preço reais ao longo do tempo",
    )
    fig.update_yaxes(rangemode="tozero")
    return fig

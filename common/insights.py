"""Análises descritivas sobre o dado acumulado -- as perguntas que a coleta
consegue responder, em funções puras (DataFrame entra, DataFrame/dict sai).

Fica separado de `stats.py` de propósito: `stats.py` decide OPORTUNIDADE
(alerta em tempo real, roda no scraper, sem pandas). Aqui é a camada de
análise retrospectiva, usada pelo dashboard -- ver aba "Resumo".

Toda função devolve o número junto com a amostra que o sustenta, e nenhuma
tenta esconder limitação: o que é descritivo não vira causal.
"""

import pandas as pd

from common.config import settings
from common.stats import margem_e_confiavel, titulo_conflita_com_modelo

FAIXAS_RAZAO = [0, 0.75, 0.90, 1.10, 1.25, float("inf")]
ROTULOS_FAIXAS = ["≤75%", "75–90%", "90–110%", "110–125%", ">125%"]


def com_razao_mediana(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona `confiavel` (mesma régua de `margem_e_confiavel`, incluindo
    o conflito título x modelo) e `razao` = preço / mediana do grupo.

    A mediana aqui é HISTÓRICA -- calculada sobre todos os anúncios já
    vistos do grupo, ativos ou não -- porque a análise é retrospectiva
    (o anúncio que sumiu há 5 dias também precisa de uma régua). Grupos
    abaixo da amostra mínima ficam com `razao` NaN, nunca inventada."""
    df = df.copy()
    modelos = df["modelo"] if "modelo" in df else [None] * len(df)
    df["confiavel"] = [
        margem_e_confiavel(co, ti, pr if pd.notna(pr) else None, ca, mo if pd.notna(mo) else None)
        for co, ti, pr, ca, mo in zip(df["condicao"], df["titulo"], df["preco"], df["categoria"], modelos)
    ]
    validos = df[df["confiavel"] & df["preco"].notna()]
    stats = validos.groupby(["categoria", "grupo"])["preco"].agg(["median", "count"])
    stats = stats[stats["count"] >= settings.oportunidade_amostra_minima]
    df = df.join(stats["median"].rename("mediana_grupo"), on=["categoria", "grupo"])
    df["razao"] = df["preco"] / df["mediana_grupo"]
    df.loc[~df["confiavel"], "razao"] = float("nan")
    return df


def _cv_mediano(df: pd.DataFrame, chaves: list[str]) -> float:
    g = df.groupby(chaves)["preco"].agg(["std", "mean", "count"])
    g = g[g["count"] >= settings.oportunidade_amostra_minima]
    return float((g["std"] / g["mean"]).median()) if not g.empty else float("nan")


def dispersao_por_categoria(df: pd.DataFrame) -> pd.DataFrame:
    """Por categoria: quão espalhados são os preços DENTRO de um mesmo grupo.
    `cv_mediano` = mediana (entre grupos) do coeficiente de variação;
    `pct_abaixo_limiar` = % dos anúncios com preço ≤ limiar da mediana do
    grupo -- o que o sistema chamaria de oportunidade.

    Dispersão alta NÃO é oportunidade: pode ser só grupo heterogêneo
    (monitor 'marca + tipo' mistura 24" e 32"). Por isso vem a coluna
    `cv_refinado` -- o mesmo CV depois de separar o grupo por polegadas
    (só monitor) -- pra separar dispersão de mercado de dispersão de grupo
    mal definido."""
    base = com_razao_mediana(df)
    linhas = []
    for categoria, sub in base.groupby("categoria"):
        validos = sub[sub["confiavel"] & sub["preco"].notna() & sub["razao"].notna()]
        if validos.empty:
            continue
        refinado = float("nan")
        if categoria == "monitor" and validos["polegadas"].notna().any():
            refinado = _cv_mediano(validos.dropna(subset=["polegadas"]), ["grupo", "polegadas"])
        linhas.append({
            "categoria": categoria,
            "anuncios": len(validos),
            "grupos": validos["grupo"].nunique(),
            "cv_mediano": _cv_mediano(validos, ["grupo"]),
            "cv_refinado": refinado,
            "pct_abaixo_limiar": float((validos["razao"] <= settings.oportunidade_limiar).mean() * 100),
        })
    return pd.DataFrame(linhas)


def tempo_no_ar_por_faixa(df: pd.DataFrame, categoria: str) -> pd.DataFrame:
    """Mediana de dias no ar (primeiro_visto_em -> removido_em) de anúncios
    JÁ REMOVIDOS, por faixa de preço em relação à mediana do grupo.

    Leitura correta: sinal DESCRITIVO de giro. Não diferencia venda de
    desanúncio e só enxerga anúncio que já saiu (quem ainda está no ar não
    entra -- viés de sobrevivência que puxa todas as faixas pra baixo).
    `removido_em` é o último avistamento, então a duração é um piso
    aproximado, e a coleta irregular (PC pessoal) quantiza os valores."""
    sub = com_razao_mediana(df[df["categoria"] == categoria])
    sub = sub[(sub["ativo"] == 0) & sub["removido_em"].notna() & sub["razao"].notna()].copy()
    if sub.empty:
        return pd.DataFrame(columns=["faixa", "anuncios", "mediana_dias"])
    sub["dias"] = (
        pd.to_datetime(sub["removido_em"]) - pd.to_datetime(sub["primeiro_visto_em"])
    ).dt.total_seconds() / 86400
    sub = sub[sub["dias"] >= 0]
    sub["faixa"] = pd.cut(sub["razao"], FAIXAS_RAZAO, labels=ROTULOS_FAIXAS)
    out = (
        sub.groupby("faixa", observed=False)["dias"]
        .agg(anuncios="count", mediana_dias="median")
        .reset_index()
    )
    out["faixa"] = out["faixa"].astype(str)
    return out


def resumo_quedas(quedas: pd.DataFrame, total_anuncios: int) -> dict:
    """Quedas de preço REAIS (preço novo < anterior, anterior > 0) -- mesma
    regra do gráfico de tendência. Devolve a mediana da queda em %, pra
    comparar com `desconto_negociacao_esperado` (parâmetro que o sistema
    assume, não mede)."""
    if quedas.empty:
        return {"quedas": 0, "anuncios_com_queda": 0, "mediana_pct": None, "pct_anuncios": None}
    q = quedas[(quedas["preco_anterior"] > 0) & (quedas["preco"] < quedas["preco_anterior"])].copy()
    if q.empty:
        return {"quedas": 0, "anuncios_com_queda": 0, "mediana_pct": None, "pct_anuncios": None}
    pct = (q["preco_anterior"] - q["preco"]) / q["preco_anterior"] * 100
    distintos = q["url"].nunique() if "url" in q else len(q)
    return {
        "quedas": len(q),
        "anuncios_com_queda": int(distintos),
        "mediana_pct": float(pct.median()),
        "pct_anuncios": float(distintos / total_anuncios * 100) if total_anuncios else None,
    }


def conflitos_titulo_modelo(df: pd.DataFrame) -> pd.DataFrame:
    """Anúncios cujo título cita uma geração de iPhone diferente do campo
    estruturado `modelo` -- excluídos da mediana (ver `stats.py`)."""
    if "modelo" not in df:
        return df.iloc[0:0]
    mascara = [titulo_conflita_com_modelo(t, m) for t, m in zip(df["titulo"], df["modelo"])]
    return df[mascara]


def cobertura_coleta(coletas: pd.DataFrame) -> dict:
    """Quantos dias da janela tiveram pelo menos uma rodada -- o número
    honesto de disponibilidade de uma coleta que roda num PC pessoal."""
    if coletas.empty:
        return {"rodadas": 0, "dias_com_coleta": 0, "dias_janela": 0}
    datas = pd.to_datetime(coletas["coletado_em"])
    dias_janela = (datas.max().normalize() - datas.min().normalize()).days + 1
    return {
        "rodadas": len(coletas),
        "dias_com_coleta": int(datas.dt.normalize().nunique()),
        "dias_janela": int(dias_janela),
    }

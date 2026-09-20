"""Saída do banco pra Power BI / Excel -- tabelas planas em CSV (`;` e vírgula
decimal, UTF-8 com BOM: abre certo no Excel em português sem configurar
nada). Separa fato (o que aconteceu) de dimensão (o que descreve), o
desenho que ferramenta de BI espera.

`anuncios` já vai com `razao_mediana` e `confiavel` calculados pela mesma
régua do sistema (`common/insights.py`), pra o BI não reimplementar regra
de negócio -- e divergir dela sem ninguém notar."""

from pathlib import Path

import pandas as pd

from common.insights import com_razao_mediana
from common.storage import DDL_CONFERENCIAS, get_connection


def tabelas_para_bi() -> dict[str, pd.DataFrame]:
    with get_connection() as conn:
        conn.executescript(DDL_CONFERENCIAS)
        anuncios = pd.read_sql_query("SELECT * FROM anuncios", conn)
        tabelas = {
            "fato_historico_precos": pd.read_sql_query("SELECT * FROM historico_precos", conn),
            "fato_coletas": pd.read_sql_query("SELECT * FROM coletas", conn),
            "fato_medianas_diarias": pd.read_sql_query("SELECT * FROM medianas_diarias", conn),
            "fato_conferencias_alertas": pd.read_sql_query("SELECT * FROM conferencias", conn),
        }
    enriquecido = com_razao_mediana(anuncios).rename(
        columns={"razao": "razao_mediana", "mediana_grupo": "mediana_do_grupo"}
    )
    return {"dim_anuncios": enriquecido, **tabelas}


def para_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def exportar_para_pasta(pasta: str | Path) -> dict[str, int]:
    """Grava um CSV por tabela. Devolve {nome: linhas}."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    resumo = {}
    for nome, df in tabelas_para_bi().items():
        (pasta / f"{nome}.csv").write_bytes(para_csv_bytes(df))
        resumo[nome] = len(df)
    return resumo

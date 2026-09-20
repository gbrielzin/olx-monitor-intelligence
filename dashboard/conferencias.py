"""Conferência manual de alertas -- o usuário abre o anúncio de verdade e
anota se a oportunidade era real. Base da métrica de PRECISÃO dos alertas
(aba Resumo / Oportunidades), o número que separa "o sistema apita" de "o
sistema acerta"."""

from datetime import datetime, timezone

import pandas as pd

from common.storage import DDL_CONFERENCIAS, get_connection

VEREDITOS = {"real": "Oportunidade real", "falso": "Falso alarme", "inconclusivo": "Não deu pra saber"}
MOTIVOS_FALSO = [
    "Defeito citado na descrição",
    "iCloud / bloqueio",
    "Bateria pior que o informado",
    "Anúncio já vendido",
    "Golpe ou erro de preço",
    "Modelo/armazenamento errado",
    "Outro",
]


def _garantir_tabela(conn) -> None:
    conn.executescript(DDL_CONFERENCIAS)


def registrar_conferencia(
    listing_id: int, veredito: str, titulo: str | None = None, preco: float | None = None,
    mediana_grupo: float | None = None, margem_pct: float | None = None, motivo: str | None = None,
) -> None:
    if veredito not in VEREDITOS:
        raise ValueError(f"veredito inválido: {veredito}")
    with get_connection() as conn:
        _garantir_tabela(conn)
        conn.execute(
            "INSERT INTO conferencias (listing_id, titulo, preco, mediana_grupo, margem_pct, veredito, motivo, conferido_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (listing_id, titulo, preco, mediana_grupo, margem_pct, veredito, motivo,
             datetime.now(timezone.utc).isoformat()),
        )


def carregar_conferencias() -> pd.DataFrame:
    with get_connection() as conn:
        _garantir_tabela(conn)
        return pd.read_sql_query("SELECT * FROM conferencias ORDER BY conferido_em DESC", conn)

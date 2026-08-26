"""Registro manual de compra/revenda -- preenchido pelo usuário no
dashboard, não pelo scraper. É o que finalmente permite comparar a
margem que o sistema estimou com a margem que aconteceu de verdade,
base real pra calibrar oportunidade_limiar/desconto_negociacao_esperado
mais pra frente (ver common/config.py)."""

from datetime import date, datetime, timezone

import pandas as pd

from common.storage import get_connection


def registrar_compra(
    titulo: str, preco_pago: float, categoria: str | None = None,
    url: str | None = None, comprador: str | None = None,
) -> None:
    agora = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO vendas (titulo, categoria, url, preco_pago, comprador, data_compra, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (titulo, categoria, url, preco_pago, comprador, date.today().isoformat(), agora),
        )


def marcar_como_vendido(id_: int, preco_revenda: float, comprador: str | None = None) -> None:
    with get_connection() as conn:
        if comprador:
            conn.execute(
                "UPDATE vendas SET preco_revenda=?, data_venda=?, comprador=? WHERE id=?",
                (preco_revenda, date.today().isoformat(), comprador, id_),
            )
        else:
            conn.execute(
                "UPDATE vendas SET preco_revenda=?, data_venda=? WHERE id=?",
                (preco_revenda, date.today().isoformat(), id_),
            )


def carregar_vendas() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query("SELECT * FROM vendas ORDER BY data_compra DESC", conn)

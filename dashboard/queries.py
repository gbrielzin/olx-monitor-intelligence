import pandas as pd

from common.storage import get_connection


def carregar_ativos() -> pd.DataFrame:
    """Estado atual: 1 linha por anúncio ainda ativo. Sempre em dia —
    não depende de nenhuma rodada de coleta específica ter terminado,
    diferente do antigo 'pega a última coletado_em'."""
    with get_connection() as conn:
        return pd.read_sql_query("SELECT * FROM anuncios WHERE ativo = 1", conn)


def carregar_quedas_precos(dias: int = 30) -> pd.DataFrame:
    """Histórico de quedas de preço de verdade — 1 linha por evento de
    queda (não por rodada de coleta), já cruzado com os dados do anúncio.
    Existe graças ao schema novo: antes disso seria só ruído duplicado."""
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT h.registrado_em, h.preco, h.preco_anterior, a.titulo, a.marca,
                   a.tipo_monitor, a.condicao, a.municipio, a.url
            FROM historico_precos h
            JOIN anuncios a ON a.listing_id = h.listing_id AND a.plataforma = h.plataforma
            WHERE h.preco_anterior IS NOT NULL AND h.registrado_em >= datetime('now', ?)
            ORDER BY h.registrado_em
            """,
            conn,
            params=(f"-{dias} days",),
        )

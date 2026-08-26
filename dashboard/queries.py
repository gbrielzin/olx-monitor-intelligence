import pandas as pd

from common.storage import get_connection


def carregar_ativos() -> pd.DataFrame:
    """Estado atual: 1 linha por anúncio ainda ativo. Sempre em dia —
    não depende de nenhuma rodada de coleta específica ter terminado,
    diferente do antigo 'pega a última coletado_em'."""
    with get_connection() as conn:
        return pd.read_sql_query("SELECT * FROM anuncios WHERE ativo = 1", conn)

import pandas as pd

from common.storage import get_connection


def carregar_ativos(categoria: str | None = None) -> pd.DataFrame:
    """Estado atual: 1 linha por anúncio ainda ativo. Sempre em dia —
    não depende de nenhuma rodada de coleta específica ter terminado,
    diferente do antigo 'pega a última coletado_em'. `categoria=None`
    traz todas as categorias juntas (monitor + iPhone)."""
    with get_connection() as conn:
        if categoria is None:
            return pd.read_sql_query("SELECT * FROM anuncios WHERE ativo = 1", conn)
        return pd.read_sql_query(
            "SELECT * FROM anuncios WHERE ativo = 1 AND categoria = ?", conn, params=(categoria,)
        )


def carregar_quedas_precos(dias: int = 30, categoria: str | None = None) -> pd.DataFrame:
    """Histórico de quedas de preço de verdade — 1 linha por evento de
    queda (não por rodada de coleta), já cruzado com os dados do anúncio.
    Existe graças ao schema novo: antes disso seria só ruído duplicado."""
    query = """
        SELECT h.registrado_em, h.preco, h.preco_anterior, a.titulo, a.categoria, a.marca,
               a.tipo_monitor, a.condicao, a.municipio, a.url
        FROM historico_precos h
        JOIN anuncios a ON a.listing_id = h.listing_id AND a.plataforma = h.plataforma
        WHERE h.preco_anterior IS NOT NULL AND h.registrado_em >= datetime('now', ?)
    """
    params: list = [f"-{dias} days"]
    if categoria is not None:
        query += " AND a.categoria = ?"
        params.append(categoria)
    query += " ORDER BY h.registrado_em"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)

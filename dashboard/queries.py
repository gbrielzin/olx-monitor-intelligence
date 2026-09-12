import pandas as pd

from common.storage import get_connection


def carregar_ativos(categoria: str | None = None, uf: str | None = None) -> pd.DataFrame:
    """Estado atual: 1 linha por anúncio ainda ativo. Sempre em dia —
    não depende de nenhuma rodada de coleta específica ter terminado,
    diferente do antigo 'pega a última coletado_em'. `categoria=None`
    traz todas as categorias juntas; `uf=None` traz todas as regiões
    (só relevante pra iPhone, que roda em mais de um estado)."""
    query = "SELECT * FROM anuncios WHERE ativo = 1"
    params: list = []
    if categoria is not None:
        query += " AND categoria = ?"
        params.append(categoria)
    if uf is not None:
        query += " AND uf = ?"
        params.append(uf)
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def carregar_quedas_precos(dias: int = 30, categoria: str | None = None, uf: str | None = None) -> pd.DataFrame:
    """Histórico de quedas de preço de verdade — 1 linha por evento de
    queda (não por rodada de coleta), já cruzado com os dados do anúncio.
    Existe graças ao schema novo: antes disso seria só ruído duplicado."""
    query = """
        SELECT h.registrado_em, h.preco, h.preco_anterior, a.titulo, a.categoria, a.marca,
               a.tipo_monitor, a.uf, a.condicao, a.municipio, a.url
        FROM historico_precos h
        JOIN anuncios a ON a.listing_id = h.listing_id AND a.plataforma = h.plataforma
        WHERE h.preco_anterior IS NOT NULL AND h.registrado_em >= datetime('now', ?)
    """
    params: list = [f"-{dias} days"]
    if categoria is not None:
        query += " AND a.categoria = ?"
        params.append(categoria)
    if uf is not None:
        query += " AND a.uf = ?"
        params.append(uf)
    query += " ORDER BY h.registrado_em"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def carregar_coletas(dias: int = 7, categoria: str | None = None, uf: str | None = None) -> pd.DataFrame:
    """Histórico de rodadas de coleta (1 linha por rodada/categoria/região) --
    gravado em toda rodada (`common/storage.py:upsert_ads`), mas não
    aparecia em lugar nenhum do dashboard antes da aba de auditoria. É a
    mesma régua que `scraper/sanity.py` usa (com_preco/total_anuncios,
    total_anuncios vs média histórica DESSA região) pra decidir se descarta
    a rodada -- só que visível aqui, em vez de só virar alerta no Telegram
    quando falha."""
    query = "SELECT * FROM coletas WHERE coletado_em >= datetime('now', ?)"
    params: list = [f"-{dias} days"]
    if categoria is not None:
        query += " AND categoria = ?"
        params.append(categoria)
    if uf is not None:
        query += " AND uf = ?"
        params.append(uf)
    query += " ORDER BY coletado_em DESC"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def carregar_novidades(dias: int = 1, categoria: str | None = None, uf: str | None = None) -> pd.DataFrame:
    """Anúncios vistos pela 1a vez nos últimos `dias` dias
    (`primeiro_visto_em`), com o resultado da auditoria de IA quando existe
    (LEFT JOIN -- `auditoria_ia` só tem linha se settings.ia_auditoria_ativa
    estava ligado quando o anúncio foi visto). Pensado pra auditoria manual:
    bater o olho no que foi capturado e comparar com o anúncio real na
    OLX, do jeito que o usuário já faz pras oportunidades."""
    query = """
        SELECT a.primeiro_visto_em, a.categoria, a.titulo, a.preco, a.grupo,
               a.uf, a.condicao, a.municipio, a.url,
               i.inconsistente, i.motivo, i.resumo
        FROM anuncios a
        LEFT JOIN auditoria_ia i
            ON i.listing_id = a.listing_id AND i.plataforma = a.plataforma
        WHERE a.primeiro_visto_em >= datetime('now', ?)
    """
    params: list = [f"-{dias} days"]
    if categoria is not None:
        query += " AND a.categoria = ?"
        params.append(categoria)
    if uf is not None:
        query += " AND a.uf = ?"
        params.append(uf)
    query += " ORDER BY a.primeiro_visto_em DESC"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)

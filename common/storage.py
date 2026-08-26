"""Persistência em SQLite, modo WAL.

WAL permite um escritor (scraper) e múltiplos leitores (dashboard)
simultâneos sem 'database is locked' — é o motivo de existir este
módulo em vez de cada serviço abrir sua própria conexão crua.

Uma linha por anúncio (chave: listing_id + plataforma), não uma linha
por rodada de coleta. Cada anúncio tem uma URL única em `anuncios`
(constraint UNIQUE) que é sempre atualizada pro estado mais recente —
uma rodada em que nada mudou não grava linha nova nenhuma. A tabela
`historico_precos` só recebe uma linha quando o preço CAI: alta de
preço atualiza `anuncios.preco` (tem que refletir a verdade) mas não
gera linha de histórico — só queda é sinal de oportunidade.

Isso é decisão de produto, não só limpeza de banco: antes, cada
anúncio parado no ar por N rodadas virava N amostras idênticas na
mediana usada por `common/stats.py`, inflando artificialmente a
'amostra mínima' e enviesando a mediana pro preço de quem está há
mais tempo anunciado — o oposto de 'preço justo de mercado agora'.
Ver `oportunidade_amostra_minima` em config.py.

Multi-categoria (monitor, iPhone, ...): uma tabela só, discriminada por
`categoria`, com as colunas de specs de TODAS as categorias (nullable —
um anúncio de iPhone não usa hz_exato, um de monitor não usa modelo).
`grupo` é a chave de comparabilidade calculada por cada schema (ver
common/schema.py) — todo o resto deste módulo (upsert, mediana, sanidade)
agrupa por (categoria, grupo) sem precisar conhecer os campos de cada
categoria. Ver MonitorAd.grupo / IphoneAd.grupo.
"""

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from common.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anuncios (
    listing_id INTEGER NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'olx',
    categoria TEXT NOT NULL DEFAULT 'monitor',
    grupo TEXT NOT NULL DEFAULT '',
    titulo TEXT NOT NULL,
    preco REAL,
    preco_antigo REAL,
    url TEXT NOT NULL,
    data_publicacao TEXT NOT NULL,
    municipio TEXT,
    bairro TEXT,
    marca TEXT,
    condicao TEXT,
    -- specs de monitor
    polegadas TEXT,
    resolucao_max TEXT,
    faixa_hz TEXT,
    hz_exato INTEGER,
    tipo_tela TEXT,
    tipo_monitor TEXT,
    curvo INTEGER NOT NULL DEFAULT 0,
    -- specs de iphone
    modelo TEXT,
    armazenamento_gb INTEGER,
    cor TEXT,
    saude_bateria TEXT,
    vendedor_nome TEXT,
    vendedor_nota REAL,
    primeiro_visto_em TEXT NOT NULL,
    ultimo_visto_em TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1,
    removido_em TEXT,
    PRIMARY KEY (listing_id, plataforma)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_anuncios_url ON anuncios (url);
CREATE INDEX IF NOT EXISTS idx_anuncios_grupo ON anuncios (categoria, grupo);
CREATE INDEX IF NOT EXISTS idx_anuncios_ativo ON anuncios (ativo);

CREATE TABLE IF NOT EXISTS historico_precos (
    listing_id INTEGER NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'olx',
    preco REAL NOT NULL,
    preco_anterior REAL,
    registrado_em TEXT NOT NULL,
    PRIMARY KEY (listing_id, plataforma, registrado_em)
);

CREATE INDEX IF NOT EXISTS idx_historico_listing ON historico_precos (listing_id, plataforma);

CREATE TABLE IF NOT EXISTS coletas (
    coletado_em TEXT NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'olx',
    categoria TEXT NOT NULL DEFAULT 'monitor',
    total_anuncios INTEGER NOT NULL,
    novos INTEGER NOT NULL,
    quedas_preco INTEGER NOT NULL,
    com_preco INTEGER NOT NULL,
    PRIMARY KEY (coletado_em, plataforma, categoria)
);
"""

_COLUNAS_ANUNCIO = (
    "listing_id, plataforma, categoria, grupo, titulo, preco, preco_antigo, url, data_publicacao, "
    "municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz, "
    "hz_exato, tipo_tela, tipo_monitor, curvo, modelo, armazenamento_gb, cor, saude_bateria, "
    "vendedor_nome, vendedor_nota"
)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        _migrar_schema_legado_se_necessario(conn)
        _adiciona_multi_categoria_se_necessario(conn)
        conn.executescript(_SCHEMA)


def _migrar_schema_legado_se_necessario(conn: sqlite3.Connection) -> None:
    """Auto-detecta o schema antigo (1 linha por coleta, PK incluindo
    coletado_em) e migra pro schema novo (1 linha por anúncio) na
    primeira subida com o código novo — sem passo manual, sem depender
    de alguém lembrar de rodar uma migration antes do deploy.

    Idempotente: se `anuncios` não existe (banco novo) ou já tem
    `primeiro_visto_em` (já migrado), não faz nada.
    """
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='anuncios'"
    ).fetchone()
    if existe is None:
        return
    colunas = {r[1] for r in conn.execute("PRAGMA table_info(anuncios)").fetchall()}
    if "primeiro_visto_em" in colunas:
        return

    total_legado = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    logger.warning(
        "Schema antigo detectado (%d linhas, 1 por coleta) — migrando para "
        "1 linha por anúncio. A tabela antiga é preservada como "
        "'anuncios_legado_pre_migracao'.",
        total_legado,
    )

    conn.execute("DROP INDEX IF EXISTS idx_anuncios_coleta")
    conn.execute("DROP INDEX IF EXISTS idx_anuncios_modelo")
    conn.execute("DROP INDEX IF EXISTS idx_anuncios_listing")
    conn.execute("ALTER TABLE anuncios RENAME TO anuncios_legado_pre_migracao")
    conn.executescript(_SCHEMA)
    _backfill_de_legado(conn)

    total_novo = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    total_quedas = conn.execute(
        "SELECT COUNT(*) FROM historico_precos WHERE preco_anterior IS NOT NULL"
    ).fetchone()[0]
    logger.warning(
        "Migração concluída: %d anúncios distintos recuperados de %d linhas "
        "legadas (%d eram duplicatas exatas). %d quedas de preço reais "
        "identificadas no histórico.",
        total_novo, total_legado, total_legado - total_novo, total_quedas,
    )


def _backfill_de_legado(conn: sqlite3.Connection) -> None:
    linhas = conn.execute(
        """
        SELECT listing_id, plataforma, titulo, preco, preco_antigo, url,
               data_publicacao, municipio, bairro, marca, condicao, polegadas,
               resolucao_max, faixa_hz, hz_exato, tipo_tela, tipo_monitor,
               curvo, vendedor_nome, vendedor_nota, coletado_em
        FROM anuncios_legado_pre_migracao
        ORDER BY listing_id, plataforma, coletado_em
        """
    ).fetchall()
    if not linhas:
        return

    ultima_coleta_global = conn.execute(
        "SELECT MAX(coletado_em) FROM anuncios_legado_pre_migracao"
    ).fetchone()[0]

    estado: dict[tuple[int, str], dict] = {}
    historico: list[tuple] = []

    for l in linhas:
        (listing_id, plataforma, titulo, preco, preco_antigo, url, data_publicacao,
         municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz,
         hz_exato, tipo_tela, tipo_monitor, curvo, vendedor_nome, vendedor_nota,
         coletado_em) = l

        chave = (listing_id, plataforma)
        anterior = estado.get(chave)
        preco_anterior = anterior["preco"] if anterior else None

        if anterior is None:
            historico.append((listing_id, plataforma, preco, None, coletado_em))
        elif preco is not None and preco_anterior is not None and preco < preco_anterior:
            historico.append((listing_id, plataforma, preco, preco_anterior, coletado_em))

        estado[chave] = {
            "titulo": titulo, "preco": preco, "preco_antigo": preco_antigo, "url": url,
            "data_publicacao": data_publicacao, "municipio": municipio, "bairro": bairro,
            "marca": marca, "condicao": condicao, "polegadas": polegadas,
            "resolucao_max": resolucao_max, "faixa_hz": faixa_hz, "hz_exato": hz_exato,
            "tipo_tela": tipo_tela, "tipo_monitor": tipo_monitor, "curvo": curvo,
            "vendedor_nome": vendedor_nome, "vendedor_nota": vendedor_nota,
            "primeiro_visto_em": anterior["primeiro_visto_em"] if anterior else coletado_em,
            "ultimo_visto_em": coletado_em,
        }

    # Todo o schema legado é de antes de existir categoria -- é tudo
    # monitor, sempre.
    anuncios_rows = []
    for (listing_id, plataforma), d in estado.items():
        ativo = 1 if d["ultimo_visto_em"] == ultima_coleta_global else 0
        removido_em = None if ativo else d["ultimo_visto_em"]
        grupo = f"{d['marca'] or '?'} · {d['tipo_monitor'] or '?'}"
        anuncios_rows.append((
            listing_id, plataforma, "monitor", grupo, d["titulo"], d["preco"], d["preco_antigo"],
            d["url"], d["data_publicacao"], d["municipio"], d["bairro"], d["marca"], d["condicao"],
            d["polegadas"], d["resolucao_max"], d["faixa_hz"], d["hz_exato"], d["tipo_tela"],
            d["tipo_monitor"], d["curvo"], None, None, None, None,
            d["vendedor_nome"], d["vendedor_nota"],
            d["primeiro_visto_em"], d["ultimo_visto_em"], ativo, removido_em,
        ))

    conn.executemany(
        f"""
        INSERT INTO anuncios (
            {_COLUNAS_ANUNCIO}, primeiro_visto_em, ultimo_visto_em, ativo, removido_em
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        anuncios_rows,
    )
    if historico:
        conn.executemany(
            """
            INSERT INTO historico_precos
                (listing_id, plataforma, preco, preco_anterior, registrado_em)
            VALUES (?,?,?,?,?)
            """,
            historico,
        )

    coletas_rows = conn.execute(
        """
        SELECT coletado_em, plataforma, COUNT(*),
               SUM(CASE WHEN preco IS NOT NULL THEN 1 ELSE 0 END)
        FROM anuncios_legado_pre_migracao
        GROUP BY coletado_em, plataforma
        """
    ).fetchall()
    conn.executemany(
        """
        INSERT OR IGNORE INTO coletas
            (coletado_em, plataforma, categoria, total_anuncios, novos, quedas_preco, com_preco)
        VALUES (?, ?, 'monitor', ?, 0, 0, ?)
        """,
        coletas_rows,
    )


def _adiciona_multi_categoria_se_necessario(conn: sqlite3.Connection) -> None:
    """Auto-detecta um banco já no schema 'de 1 linha por anúncio' mas de
    antes do suporte a múltiplas categorias (só monitor) e adiciona as
    colunas que faltam — mesmo espírito da migração acima, sem passo
    manual. Idempotente: se `categoria` já existe em `anuncios`, não faz
    nada."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='anuncios'"
    ).fetchone()
    if existe is None:
        return
    colunas = {r[1] for r in conn.execute("PRAGMA table_info(anuncios)").fetchall()}
    if "categoria" in colunas:
        return

    logger.warning("Adicionando suporte a categoria/grupo em anúncios existentes (só monitor até agora)...")

    conn.execute("ALTER TABLE anuncios ADD COLUMN categoria TEXT NOT NULL DEFAULT 'monitor'")
    conn.execute("ALTER TABLE anuncios ADD COLUMN grupo TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE anuncios ADD COLUMN modelo TEXT")
    conn.execute("ALTER TABLE anuncios ADD COLUMN armazenamento_gb INTEGER")
    conn.execute("ALTER TABLE anuncios ADD COLUMN cor TEXT")
    conn.execute("ALTER TABLE anuncios ADD COLUMN saude_bateria TEXT")
    conn.execute(
        "UPDATE anuncios SET grupo = COALESCE(marca, '?') || ' · ' || COALESCE(tipo_monitor, '?')"
    )
    conn.execute("DROP INDEX IF EXISTS idx_anuncios_modelo")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anuncios_grupo ON anuncios (categoria, grupo)")

    colunas_coletas = {r[1] for r in conn.execute("PRAGMA table_info(coletas)").fetchall()}
    if "categoria" not in colunas_coletas:
        conn.execute("ALTER TABLE coletas ADD COLUMN categoria TEXT NOT NULL DEFAULT 'monitor'")

    logger.warning("Suporte a categoria/grupo adicionado — anúncios existentes marcados como 'monitor'.")


@dataclass
class ColetaResultado:
    """Efeito de uma rodada sobre o estado salvo — os dois gatilhos de
    alerta (`scraper/diff.py` traduz os ids de volta pra objetos do
    schema usado nessa rodada)."""

    novos: set[int] = field(default_factory=set)
    quedas: dict[int, tuple[float, float]] = field(default_factory=dict)  # id -> (novo, anterior)


def snapshot_estado(plataforma: str = "olx", categoria: str = "monitor") -> dict[int, tuple[float | None, bool]]:
    """listing_id -> (preco atual, ativo) de tudo que já foi visto NESSA
    categoria — usado por upsert_ads pra decidir o que é novo/queda/sumiço
    antes de sobrescrever o estado. Filtrar por categoria aqui não é
    opcional: sem isso, rodar a coleta de iPhone marcaria todo monitor
    ativo como "sumido" (não veio nesta rodada de iPhone) e vice-versa."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT listing_id, preco, ativo FROM anuncios WHERE plataforma = ? AND categoria = ?",
            (plataforma, categoria),
        )
        return {r[0]: (r[1], bool(r[2])) for r in cursor.fetchall()}


def upsert_ads(ads: list, plataforma: str = "olx") -> ColetaResultado:
    """Grava o estado mais recente de cada anúncio — 1 linha por
    (listing_id, plataforma) em `anuncios`, sempre. Preço só vira linha
    nova em `historico_precos` quando CAI em relação ao valor salvo
    (alta de preço atualiza `anuncios.preco` sem gerar histórico — não
    é sinal de oportunidade, não precisa de registro).

    `ads` é uma lista de MonitorAd OU de IphoneAd (nunca misturado numa
    mesma chamada — cada categoria roda sua própria coleta) — qualquer
    objeto com os campos comuns (listing_id, categoria, grupo, titulo,
    preco, url, ...) serve, os campos específicos de categoria (hz_exato,
    modelo, ...) são lidos com getattr() porque só existem num dos dois.

    Um anúncio que suma desta rodada (estava ativo, não veio na lista)
    é marcado `ativo=0` — deixa de contar na mediana de `common/stats.py`
    e fica disponível pra análise de 'tempo até sumir do ar'.
    """
    if not ads:
        return ColetaResultado()

    categoria = ads[0].categoria
    momento = ads[0].coletado_em.isoformat()
    estado_antes = snapshot_estado(plataforma, categoria)
    resultado = ColetaResultado()

    upsert_rows = []
    historico_rows = []
    ids_recebidos = set()

    for a in ads:
        ids_recebidos.add(a.listing_id)
        preco_anterior, estava_ativo = estado_antes.get(a.listing_id, (None, False))

        if a.listing_id not in estado_antes or not estava_ativo:
            resultado.novos.add(a.listing_id)
            historico_rows.append((a.listing_id, plataforma, a.preco, preco_anterior, momento))
        elif a.preco is not None and preco_anterior is not None and a.preco < preco_anterior:
            resultado.quedas[a.listing_id] = (a.preco, preco_anterior)
            historico_rows.append((a.listing_id, plataforma, a.preco, preco_anterior, momento))

        upsert_rows.append((
            a.listing_id, plataforma, a.categoria, a.grupo, a.titulo, a.preco, a.preco_antigo, a.url,
            a.data_publicacao.isoformat(), a.municipio, a.bairro, a.marca, a.condicao,
            getattr(a, "polegadas", None), getattr(a, "resolucao_max", None), getattr(a, "faixa_hz", None),
            getattr(a, "hz_exato", None), getattr(a, "tipo_tela", None), getattr(a, "tipo_monitor", None),
            int(getattr(a, "curvo", False)),
            getattr(a, "modelo", None), getattr(a, "armazenamento_gb", None),
            getattr(a, "cor", None), getattr(a, "saude_bateria", None),
            a.vendedor_nome, a.vendedor_nota,
            momento, momento,
        ))

    com_preco = sum(1 for a in ads if a.preco is not None)

    with get_connection() as conn:
        conn.executemany(
            f"""
            INSERT INTO anuncios (
                {_COLUNAS_ANUNCIO}, primeiro_visto_em, ultimo_visto_em, ativo, removido_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,NULL)
            ON CONFLICT (listing_id, plataforma) DO UPDATE SET
                categoria=excluded.categoria,
                grupo=excluded.grupo,
                titulo=excluded.titulo,
                preco=excluded.preco,
                preco_antigo=excluded.preco_antigo,
                url=excluded.url,
                data_publicacao=excluded.data_publicacao,
                municipio=excluded.municipio,
                bairro=excluded.bairro,
                marca=excluded.marca,
                condicao=excluded.condicao,
                polegadas=excluded.polegadas,
                resolucao_max=excluded.resolucao_max,
                faixa_hz=excluded.faixa_hz,
                hz_exato=excluded.hz_exato,
                tipo_tela=excluded.tipo_tela,
                tipo_monitor=excluded.tipo_monitor,
                curvo=excluded.curvo,
                modelo=excluded.modelo,
                armazenamento_gb=excluded.armazenamento_gb,
                cor=excluded.cor,
                saude_bateria=excluded.saude_bateria,
                vendedor_nome=excluded.vendedor_nome,
                vendedor_nota=excluded.vendedor_nota,
                ultimo_visto_em=excluded.ultimo_visto_em,
                ativo=1,
                removido_em=NULL
            """,
            upsert_rows,
        )

        if historico_rows:
            conn.executemany(
                """
                INSERT INTO historico_precos
                    (listing_id, plataforma, preco, preco_anterior, registrado_em)
                VALUES (?,?,?,?,?)
                """,
                historico_rows,
            )

        sumiram = [
            lid for lid, (_, ativo) in estado_antes.items()
            if ativo and lid not in ids_recebidos
        ]
        if sumiram:
            conn.executemany(
                "UPDATE anuncios SET ativo=0, removido_em=? WHERE listing_id=? AND plataforma=?",
                [(momento, lid, plataforma) for lid in sumiram],
            )

        conn.execute(
            """
            INSERT INTO coletas (coletado_em, plataforma, categoria, total_anuncios, novos, quedas_preco, com_preco)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (momento, plataforma, categoria, len(ads), len(resultado.novos), len(resultado.quedas), com_preco),
        )

    return resultado


def eh_minimo_historico(listing_id: int, preco: float, plataforma: str = "olx") -> bool:
    """True quando `preco` é o menor preço já registrado pra esse anúncio em
    `historico_precos` (inclui o "avistamento" inicial + toda queda já
    logada) — sinal mais forte que uma queda comum: o vendedor nunca pediu
    tão pouco por esse anúncio quanto agora. Chamar depois de upsert_ads()
    já ter gravado a linha desta rodada em historico_precos. Não precisa
    de categoria: listing_id já é único globalmente na OLX."""
    with get_connection() as conn:
        minimo = conn.execute(
            "SELECT MIN(preco) FROM historico_precos WHERE listing_id = ? AND plataforma = ?",
            (listing_id, plataforma),
        ).fetchone()[0]
    return minimo is not None and preco <= minimo


def contagem_media_ultimas_coletas(plataforma: str = "olx", categoria: str = "monitor", n: int = 5) -> float | None:
    """Média de anúncios por rodada nas últimas N coletas DESSA categoria
    — usada pelo checkpoint de sanidade pra flagrar quedas abruptas.
    Sem o filtro de categoria, misturar contagens de monitor e iPhone na
    mesma média faria o checkpoint comparar coisas diferentes."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT total_anuncios FROM coletas
            WHERE plataforma = ? AND categoria = ?
            ORDER BY coletado_em DESC
            LIMIT ?
            """,
            (plataforma, categoria, n),
        )
        contagens = [r[0] for r in cursor.fetchall()]
    if not contagens:
        return None
    return sum(contagens) / len(contagens)

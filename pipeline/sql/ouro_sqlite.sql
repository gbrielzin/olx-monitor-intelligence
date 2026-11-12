-- Camada ouro: modelo estrela (fato + dimensões) pra análise de preço.
-- Carregada a partir da prata por pipeline/ouro.py. Idempotente: rodar de
-- novo com a mesma prata não duplica nada (UNIQUE nas chaves naturais +
-- PRIMARY KEY no grão do fato).

-- Uma linha por dia do calendário que apareceu em alguma coleta.
-- data_key no formato AAAAMMDD (inteiro): ordena certo e é o padrão
-- de chave de data em data warehouse.
CREATE TABLE IF NOT EXISTS dim_data (
    data_key    INTEGER PRIMARY KEY,          -- ex.: 20261112
    data        TEXT NOT NULL UNIQUE,         -- '2026-11-12'
    ano         INTEGER NOT NULL,
    mes         INTEGER NOT NULL,
    dia         INTEGER NOT NULL,
    dia_semana  INTEGER NOT NULL,             -- 0 = segunda ... 6 = domingo
    fim_de_semana INTEGER NOT NULL            -- 1 = sábado/domingo
);

-- Modelo comparável. Chave natural = categoria + modelo + armazenamento
-- (é o mesmo recorte do `grupo` do sistema, sem a UF -- a UF fica na
-- dim_local, pra dar pra comparar o mesmo modelo entre estados).
CREATE TABLE IF NOT EXISTS dim_modelo (
    modelo_key       INTEGER PRIMARY KEY AUTOINCREMENT,
    chave_natural    TEXT NOT NULL UNIQUE,    -- 'iphone|IPHONE 13|128'
    categoria        TEXT NOT NULL,
    modelo           TEXT NOT NULL,           -- '?' quando o anúncio não informa
    armazenamento_gb INTEGER                  -- NULL quando o anúncio não informa
);

CREATE TABLE IF NOT EXISTS dim_local (
    local_key     INTEGER PRIMARY KEY AUTOINCREMENT,
    chave_natural TEXT NOT NULL UNIQUE,       -- 'ES|Vitória'
    uf            TEXT NOT NULL,
    municipio     TEXT NOT NULL               -- '?' quando o anúncio não informa
);

-- Grão: 1 linha por anúncio POR COLETA (snapshot). É o que permite ver o
-- preço de cada anúncio ao longo do tempo -- coisa que a tabela operacional
-- `anuncios` não guarda (ela só tem o estado mais recente).
CREATE TABLE IF NOT EXISTS fato_anuncio_snapshot (
    coleta_em    TEXT NOT NULL,               -- ISO 8601 UTC, igual em todo o lote da coleta
    plataforma   TEXT NOT NULL,
    listing_id   INTEGER NOT NULL,
    data_key     INTEGER NOT NULL REFERENCES dim_data (data_key),
    modelo_key   INTEGER NOT NULL REFERENCES dim_modelo (modelo_key),
    local_key    INTEGER NOT NULL REFERENCES dim_local (local_key),
    preco        REAL NOT NULL,
    preco_antigo REAL,
    condicao     TEXT,
    confiavel    INTEGER NOT NULL,            -- 1 = entra em mediana (mesma régua de common/stats.py)
    PRIMARY KEY (listing_id, plataforma, coleta_em)
);

-- Índices pensados nas consultas de pipeline/consultas_ouro.py:
-- série de preço por modelo no tempo, filtro por local e por coleta.
CREATE INDEX IF NOT EXISTS idx_fato_modelo_data ON fato_anuncio_snapshot (modelo_key, data_key);
CREATE INDEX IF NOT EXISTS idx_fato_local ON fato_anuncio_snapshot (local_key);
CREATE INDEX IF NOT EXISTS idx_fato_coleta ON fato_anuncio_snapshot (coleta_em);

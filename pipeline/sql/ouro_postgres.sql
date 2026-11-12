-- Mesmo modelo estrela de ouro_sqlite.sql, escrito pra Postgres.
-- Referência pra quando a camada ouro sair do SQLite (ex.: Postgres/RDS).
-- AINDA NÃO FOI EXECUTADO contra um Postgres de verdade -- o que roda e
-- tem teste hoje é a versão SQLite.
--
-- Diferenças em relação ao SQLite:
--   * IDENTITY no lugar de AUTOINCREMENT;
--   * TIMESTAMPTZ e DATE de verdade no lugar de TEXT;
--   * NUMERIC(10,2) pra preço (dinheiro sem erro de ponto flutuante);
--   * dá pra usar percentile_cont(0.5) pra mediana direto, sem o truque
--     de ROW_NUMBER que a consulta SQLite precisa.

CREATE TABLE IF NOT EXISTS dim_data (
    data_key      INTEGER PRIMARY KEY,
    data          DATE NOT NULL UNIQUE,
    ano           SMALLINT NOT NULL,
    mes           SMALLINT NOT NULL,
    dia           SMALLINT NOT NULL,
    dia_semana    SMALLINT NOT NULL,
    fim_de_semana BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_modelo (
    modelo_key       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chave_natural    TEXT NOT NULL UNIQUE,
    categoria        TEXT NOT NULL,
    modelo           TEXT NOT NULL,
    armazenamento_gb INTEGER
);

CREATE TABLE IF NOT EXISTS dim_local (
    local_key     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chave_natural TEXT NOT NULL UNIQUE,
    uf            CHAR(2) NOT NULL,
    municipio     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fato_anuncio_snapshot (
    coleta_em    TIMESTAMPTZ NOT NULL,
    plataforma   TEXT NOT NULL,
    listing_id   BIGINT NOT NULL,
    data_key     INTEGER NOT NULL REFERENCES dim_data (data_key),
    modelo_key   INTEGER NOT NULL REFERENCES dim_modelo (modelo_key),
    local_key    INTEGER NOT NULL REFERENCES dim_local (local_key),
    preco        NUMERIC(10, 2) NOT NULL,
    preco_antigo NUMERIC(10, 2),
    condicao     TEXT,
    confiavel    BOOLEAN NOT NULL,
    PRIMARY KEY (listing_id, plataforma, coleta_em)
);

CREATE INDEX IF NOT EXISTS idx_fato_modelo_data ON fato_anuncio_snapshot (modelo_key, data_key);
CREATE INDEX IF NOT EXISTS idx_fato_local ON fato_anuncio_snapshot (local_key);
CREATE INDEX IF NOT EXISTS idx_fato_coleta ON fato_anuncio_snapshot (coleta_em);

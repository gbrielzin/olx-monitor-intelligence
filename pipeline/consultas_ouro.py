"""Consultas analíticas sobre a camada ouro, cada uma respondendo uma
pergunta real do projeto. Todas usam window function (SQLite >= 3.28).

Rodar todas de uma vez, contra o banco ouro:

    PYTHONPATH=. OURO_DB_PATH=data/ouro.db python -m pipeline.consultas_ouro
"""

from __future__ import annotations

import sqlite3

# 1) "Quais anúncios baixaram de preço de uma coleta pra outra, e quanto?"
#    LAG pega o preço do MESMO anúncio na coleta anterior em que ele
#    apareceu. É a versão analítica do gatilho de alerta de queda
#    (common/storage.py), só que olhando o histórico inteiro, não só a
#    última rodada.
QUEDAS_DE_PRECO = """
WITH serie AS (
    SELECT
        f.listing_id,
        f.coleta_em,
        m.modelo,
        m.armazenamento_gb,
        l.uf,
        f.preco,
        LAG(f.preco) OVER (
            PARTITION BY f.listing_id, f.plataforma ORDER BY f.coleta_em
        ) AS preco_anterior
    FROM fato_anuncio_snapshot f
    JOIN dim_modelo m ON m.modelo_key = f.modelo_key
    JOIN dim_local  l ON l.local_key  = f.local_key
)
SELECT
    listing_id, coleta_em, modelo, armazenamento_gb, uf,
    preco_anterior, preco,
    ROUND(preco - preco_anterior, 2)                    AS variacao_rs,
    ROUND((preco - preco_anterior) / preco_anterior, 4) AS variacao_pct
FROM serie
WHERE preco_anterior IS NOT NULL AND preco < preco_anterior
ORDER BY variacao_pct ASC
LIMIT :limite
"""

# 2) "Na última coleta de cada UF, quais são os 3 anúncios mais baratos de
#    cada modelo?" -- ROW_NUMBER numera dentro de cada (UF, modelo) do mais
#    barato pro mais caro. Só anúncio confiável (sem defeito no título, sem
#    preço abaixo do piso, título batendo com o modelo -- mesma régua da
#    mediana do sistema), senão o "mais barato" é quase sempre sucata.
MAIS_BARATOS_POR_MODELO = """
WITH ultima_coleta AS (
    SELECT l.uf, MAX(f.coleta_em) AS coleta_em
    FROM fato_anuncio_snapshot f
    JOIN dim_local l ON l.local_key = f.local_key
    GROUP BY l.uf
),
ranqueado AS (
    SELECT
        l.uf,
        m.modelo,
        m.armazenamento_gb,
        f.listing_id,
        f.preco,
        ROW_NUMBER() OVER (
            PARTITION BY l.uf, f.modelo_key ORDER BY f.preco ASC, f.listing_id
        ) AS posicao,
        COUNT(*) OVER (PARTITION BY l.uf, f.modelo_key) AS anuncios_no_modelo
    FROM fato_anuncio_snapshot f
    JOIN dim_local  l ON l.local_key  = f.local_key
    JOIN dim_modelo m ON m.modelo_key = f.modelo_key
    JOIN ultima_coleta u ON u.uf = l.uf AND u.coleta_em = f.coleta_em
    WHERE f.confiavel = 1
)
SELECT uf, modelo, armazenamento_gb, posicao, listing_id, preco, anuncios_no_modelo
FROM ranqueado
WHERE posicao <= :top
ORDER BY uf, modelo, armazenamento_gb, posicao
"""

# 3) "Como a mediana diária de preço de cada modelo anda no tempo?" --
#    mediana do dia + média móvel de 7 dias corridos dessa mediana.
#    Dois cuidados que fazem diferença no número:
#    a) um anúncio aparece em dezenas de coletas no mesmo dia (uma a cada
#       12 min). Se a mediana usasse todo snapshot, anúncio parado no ar
#       pesaria mais que anúncio novo -- o mesmo viés que common/storage.py
#       evita. Por isso: 1 preço por anúncio por dia (o último do dia).
#    b) SQLite não tem MEDIAN(). A mediana sai de ROW_NUMBER + COUNT: pega
#       o(s) elemento(s) do meio da lista ordenada e tira a média deles.
#    A janela usa RANGE sobre julianday (7 dias CORRIDOS), não ROWS (7
#    linhas): a coleta roda num PC pessoal e tem buraco de dias -- com ROWS,
#    "7 linhas" poderiam cobrir 3 semanas sem ninguém perceber.
MEDIANA_MOVEL_7D = """
WITH ultimo_do_dia AS (
    SELECT f.modelo_key, f.data_key, f.listing_id, f.preco,
           ROW_NUMBER() OVER (
               PARTITION BY f.modelo_key, f.data_key, f.listing_id, f.plataforma
               ORDER BY f.coleta_em DESC
           ) AS rn
    FROM fato_anuncio_snapshot f
    WHERE f.confiavel = 1
),
ordenado AS (
    SELECT modelo_key, data_key, preco,
           ROW_NUMBER() OVER (PARTITION BY modelo_key, data_key ORDER BY preco) AS pos,
           COUNT(*)     OVER (PARTITION BY modelo_key, data_key)                AS n
    FROM ultimo_do_dia
    WHERE rn = 1
),
mediana_dia AS (
    SELECT modelo_key, data_key, AVG(preco) AS mediana, MAX(n) AS amostra
    FROM ordenado
    WHERE pos IN ((n + 1) / 2, (n + 2) / 2)
    GROUP BY modelo_key, data_key
)
SELECT
    m.modelo,
    m.armazenamento_gb,
    d.data,
    md.amostra,
    ROUND(md.mediana, 2) AS mediana_dia,
    ROUND(AVG(md.mediana) OVER (
        PARTITION BY md.modelo_key
        ORDER BY julianday(d.data)
        RANGE BETWEEN 6 PRECEDING AND CURRENT ROW
    ), 2) AS media_movel_7d,
    COUNT(*) OVER (
        PARTITION BY md.modelo_key
        ORDER BY julianday(d.data)
        RANGE BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS dias_com_dado_na_janela
FROM mediana_dia md
JOIN dim_modelo m ON m.modelo_key = md.modelo_key
JOIN dim_data   d ON d.data_key   = md.data_key
WHERE md.amostra >= :amostra_minima
ORDER BY m.modelo, m.armazenamento_gb, d.data
"""

CONSULTAS = {
    "quedas_de_preco": (QUEDAS_DE_PRECO, {"limite": 20}),
    "mais_baratos_por_modelo": (MAIS_BARATOS_POR_MODELO, {"top": 3}),
    "mediana_movel_7d": (MEDIANA_MOVEL_7D, {"amostra_minima": 5}),
}


def rodar(conn: sqlite3.Connection, nome: str, **params) -> list[dict]:
    sql, padrao = CONSULTAS[nome]
    cur = conn.execute(sql, {**padrao, **params})
    colunas = [c[0] for c in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


if __name__ == "__main__":
    from common.config import settings
    from pipeline.ouro import conectar

    with conectar(settings.ouro_db_path) as conn:
        for nome in CONSULTAS:
            linhas = rodar(conn, nome)
            print(f"\n== {nome}: {len(linhas)} linha(s)")
            for ln in linhas[:10]:
                print(ln)

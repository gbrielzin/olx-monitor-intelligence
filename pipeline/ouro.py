"""Camada ouro: carrega a prata num modelo estrela (fato + dimensões).

DDL em `pipeline/sql/ouro_sqlite.sql` (e a versão Postgres, de referência,
em `ouro_postgres.sql`). As consultas analíticas ficam em
`pipeline/consultas_ouro.py`.

Fica num banco SQLite próprio (`OURO_DB_PATH`), separado do banco
operacional (`DB_PATH`) que o scraper e o dashboard usam. Motivo: a ouro é
derivada -- se eu mudar o modelo, apago o arquivo e reconstruo tudo a partir
da prata com `reconstruir_do_lake()`, sem risco pro que já funciona.

Data do snapshot (`dim_data`) é a data no horário de Brasília (UTC-3 fixo;
o Brasil não tem horário de verão desde 2019). Uma coleta às 01h UTC é
22h do dia anterior pra quem anuncia -- pra análise por dia, o dia local é
o que faz sentido. Atenção: `medianas_diarias` do banco operacional usa o
dia em UTC; os dois podem diferir em coletas entre 21h e 0h de Brasília.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from pipeline.armazenamento import Armazenamento
from pipeline.camadas import ler_prata

DDL_SQLITE = (Path(__file__).parent / "sql" / "ouro_sqlite.sql").read_text(encoding="utf-8")
FUSO_BRASILIA = timezone(timedelta(hours=-3))
TABELAS_OURO = ("dim_data", "dim_modelo", "dim_local", "fato_anuncio_snapshot")


@contextmanager
def conectar(caminho: str | Path) -> Iterator[sqlite3.Connection]:
    if str(caminho) != ":memory:":
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(caminho), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.executescript(DDL_SQLITE)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _data_local(coleta_em_iso: str) -> datetime:
    return datetime.fromisoformat(coleta_em_iso).astimezone(FUSO_BRASILIA)


def _garante_data(conn: sqlite3.Connection, momento: datetime) -> int:
    d = momento.date()
    data_key = d.year * 10000 + d.month * 100 + d.day
    conn.execute(
        "INSERT OR IGNORE INTO dim_data (data_key, data, ano, mes, dia, dia_semana, fim_de_semana) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data_key, d.isoformat(), d.year, d.month, d.day, d.weekday(), int(d.weekday() >= 5)),
    )
    return data_key


def _garante_modelo(conn: sqlite3.Connection, categoria: str, modelo: str | None, gb: int | None) -> int:
    modelo = modelo or "?"
    chave = f"{categoria}|{modelo}|{gb if gb is not None else '?'}"
    conn.execute(
        "INSERT OR IGNORE INTO dim_modelo (chave_natural, categoria, modelo, armazenamento_gb) VALUES (?, ?, ?, ?)",
        (chave, categoria, modelo, gb),
    )
    return conn.execute("SELECT modelo_key FROM dim_modelo WHERE chave_natural = ?", (chave,)).fetchone()[0]


def _garante_local(conn: sqlite3.Connection, uf: str | None, municipio: str | None) -> int:
    uf = (uf or "?").upper()
    municipio = municipio or "?"
    chave = f"{uf}|{municipio}"
    conn.execute(
        "INSERT OR IGNORE INTO dim_local (chave_natural, uf, municipio) VALUES (?, ?, ?)",
        (chave, uf, municipio),
    )
    return conn.execute("SELECT local_key FROM dim_local WHERE chave_natural = ?", (chave,)).fetchone()[0]


def carregar_prata(conn: sqlite3.Connection, linhas: list[dict]) -> int:
    """Insere as linhas de prata no fato (e as dimensões que faltarem).
    Linha sem preço não entra: o fato é de preço, e `preco` é NOT NULL.
    Devolve quantas linhas NOVAS entraram no fato (0 se a coleta já tinha
    sido carregada antes -- idempotente)."""
    inseridas = 0
    for ln in linhas:
        if ln.get("preco") is None:
            continue
        data_key = _garante_data(conn, _data_local(ln["coleta_em"]))
        modelo_key = _garante_modelo(conn, ln["categoria"], ln.get("modelo"), ln.get("armazenamento_gb"))
        local_key = _garante_local(conn, ln.get("uf"), ln.get("municipio"))
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO fato_anuncio_snapshot
                (coleta_em, plataforma, listing_id, data_key, modelo_key, local_key,
                 preco, preco_antigo, condicao, confiavel)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ln["coleta_em"], ln["plataforma"], ln["listing_id"], data_key, modelo_key, local_key,
                ln["preco"], ln.get("preco_antigo"), ln.get("condicao"), int(bool(ln.get("confiavel"))),
            ),
        )
        inseridas += cur.rowcount
    return inseridas


def reconstruir_do_lake(arm: Armazenamento, conn: sqlite3.Connection, prefixo: str = "prata/") -> int:
    """Recarrega a ouro a partir de TODA a prata do lake. Como o fato é
    idempotente, serve tanto pra backfill do zero quanto pra completar o que
    faltou (ex.: uma rodada em que a etapa ouro falhou)."""
    total = 0
    for chave in arm.listar(prefixo):
        if chave.endswith(".jsonl"):
            total += carregar_prata(conn, ler_prata(arm, chave))
    return total


def publicar_csv(arm: Armazenamento, conn: sqlite3.Connection, prefixo: str = "ouro") -> list[str]:
    """Grava cada tabela da ouro como CSV no lake (`ouro/<tabela>.csv`) --
    é o que uma ferramenta de BI (Power BI/Tableau) ou o Athena leriam. Na
    mesma convenção de common/export.py: `;`, UTF-8 com BOM."""
    chaves = []
    for tabela in TABELAS_OURO:
        cur = conn.execute(f"SELECT * FROM {tabela}")  # nome vem da tupla fixa acima
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur.fetchall())
        chaves.append(arm.gravar(f"{prefixo}/{tabela}.csv", buf.getvalue().encode("utf-8-sig")))
    return chaves

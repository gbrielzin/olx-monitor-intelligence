import importlib

from common.config import settings


def _storage(tmp_path, nome):
    settings.db_path = str(tmp_path / nome)
    from common import storage

    importlib.reload(storage)
    storage.init_db()
    return storage


def test_conferencia_grava_e_le(tmp_path):
    _storage(tmp_path, "c1.db")
    from dashboard import conferencias

    importlib.reload(conferencias)
    conferencias.registrar_conferencia(1, "real", titulo="iPhone 11", preco=650.0, mediana_grupo=900.0, margem_pct=54.0)
    conferencias.registrar_conferencia(2, "falso", motivo="iCloud / bloqueio")
    df = conferencias.carregar_conferencias()
    assert sorted(df["veredito"]) == ["falso", "real"]


def test_conferencia_rejeita_veredito_invalido(tmp_path):
    _storage(tmp_path, "c2.db")
    from dashboard import conferencias

    importlib.reload(conferencias)
    try:
        conferencias.registrar_conferencia(1, "talvez")
        assert False, "deveria levantar ValueError"
    except ValueError:
        pass


def test_exportar_para_pasta_grava_csv_legivel_no_excel(tmp_path):
    _storage(tmp_path, "c3.db")
    from common import export

    importlib.reload(export)
    resumo = export.exportar_para_pasta(tmp_path / "out")
    assert set(resumo) == {
        "dim_anuncios", "fato_historico_precos", "fato_coletas",
        "fato_medianas_diarias", "fato_conferencias_alertas",
    }
    bruto = (tmp_path / "out" / "fato_coletas.csv").read_bytes()
    assert bruto.startswith(b"\xef\xbb\xbf")  # BOM: Excel em português abre sem configurar


def test_conferencia_guarda_descricao_e_migra_tabela_antiga(tmp_path):
    import sqlite3

    storage = _storage(tmp_path, "c4.db")
    # simula a versão anterior: tabela sem a coluna `descricao`
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("DROP TABLE conferencias")
        conn.execute(
            "CREATE TABLE conferencias (id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL, "
            "plataforma TEXT NOT NULL DEFAULT 'olx', titulo TEXT, preco REAL, mediana_grupo REAL, "
            "margem_pct REAL, veredito TEXT NOT NULL, motivo TEXT, conferido_em TEXT NOT NULL)"
        )
    from dashboard import conferencias

    importlib.reload(conferencias)
    conferencias.registrar_conferencia(1, "falso", motivo="iCloud / bloqueio", descricao="  preso no icloud  ")
    df = conferencias.carregar_conferencias()
    assert df.iloc[0]["descricao"] == "preso no icloud"

from datetime import datetime, timezone

from common.config import settings
from common.schema import ComputadorAd, IphoneAd, MonitorAd


def _reload_storage(tmp_path, nome: str):
    settings.db_path = str(tmp_path / nome)
    from common import storage
    import importlib

    importlib.reload(storage)  # garante que pega o db_path atualizado
    return storage


def _ad(listing_id: int, coletado_em: datetime, preco: float = 100.0, url: str | None = None):
    return MonitorAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"anuncio {listing_id}",
            "url": url or f"https://x/{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "marca": "AOC",
            "tipo_monitor": "Monitor Gamer",
            "coletado_em": coletado_em,
        }
    )


def _ad_iphone(listing_id: int, coletado_em: datetime, preco: float = 1000.0, uf: str = "ES"):
    return IphoneAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"iphone {listing_id}",
            "url": f"https://x/iphone-{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "modelo": "IPHONE 13",
            "armazenamento_gb": 128,
            "uf": uf,
            "coletado_em": coletado_em,
        }
    )


def _ad_computador(listing_id: int, coletado_em: datetime, preco: float = 900.0):
    return ComputadorAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"computador {listing_id}",
            "url": f"https://x/pc-{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "cpu_modelo": "Intel Core i5",
            "ram_gb": 8,
            "coletado_em": coletado_em,
        }
    )


def test_init_e_upsert_persistem_dados(tmp_path):
    storage = _reload_storage(tmp_path, "teste.db")
    storage.init_db()
    agora = datetime.now(timezone.utc)
    storage.upsert_ads([_ad(1, agora), _ad(2, agora)])

    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    assert total == 2


def test_mesmo_anuncio_repetido_em_varias_rodadas_nao_duplica_linha(tmp_path):
    """O caso que motivou a migração: um anúncio que continua no ar,
    com o MESMO preço, rodada após rodada, não pode virar N linhas --
    a url só existe uma vez em `anuncios`."""
    storage = _reload_storage(tmp_path, "teste_dedup.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
    coleta_3 = datetime(2026, 1, 1, 12, 24, 0, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(1, coleta_1, preco=500.0)])
    storage.upsert_ads([_ad(1, coleta_2, preco=500.0)])
    storage.upsert_ads([_ad(1, coleta_3, preco=500.0)])

    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
        total_url = conn.execute(
            "SELECT COUNT(*) FROM anuncios WHERE url = 'https://x/1'"
        ).fetchone()[0]
        ultimo_visto = conn.execute(
            "SELECT ultimo_visto_em FROM anuncios WHERE listing_id = 1"
        ).fetchone()[0]
    assert total == 1
    assert total_url == 1
    assert ultimo_visto == coleta_3.isoformat()


def test_url_e_unica_no_banco(tmp_path):
    storage = _reload_storage(tmp_path, "teste_url_unica.db")
    storage.init_db()
    with storage.get_connection() as conn:
        cols = conn.execute("PRAGMA index_list(anuncios)").fetchall()
    # pelo menos um índice UNIQUE deve existir sobre a tabela
    assert any(c[2] for c in cols)  # coluna 'unique' (1) em algum índice


def test_queda_de_preco_gera_historico_e_e_reportada(tmp_path):
    storage = _reload_storage(tmp_path, "teste_queda.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(1, coleta_1, preco=1000.0)])
    resultado = storage.upsert_ads([_ad(1, coleta_2, preco=800.0)])

    assert resultado.quedas == {1: (800.0, 1000.0)}
    assert resultado.novos == set()

    with storage.get_connection() as conn:
        preco_atual = conn.execute("SELECT preco FROM anuncios WHERE listing_id = 1").fetchone()[0]
        historico = conn.execute(
            "SELECT preco, preco_anterior FROM historico_precos WHERE listing_id = 1 ORDER BY registrado_em"
        ).fetchall()
    assert preco_atual == 800.0
    # primeira linha = "avistamento" inicial (preco_anterior NULL), segunda = a queda real
    assert historico == [(1000.0, None), (800.0, 1000.0)]


def test_alta_de_preco_atualiza_estado_mas_nao_gera_historico(tmp_path):
    storage = _reload_storage(tmp_path, "teste_alta.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(1, coleta_1, preco=800.0)])
    resultado = storage.upsert_ads([_ad(1, coleta_2, preco=900.0)])

    assert resultado.quedas == {}
    with storage.get_connection() as conn:
        preco_atual = conn.execute("SELECT preco FROM anuncios WHERE listing_id = 1").fetchone()[0]
        total_historico = conn.execute(
            "SELECT COUNT(*) FROM historico_precos WHERE listing_id = 1"
        ).fetchone()[0]
    assert preco_atual == 900.0  # reflete o preço real, mesmo sem virar "oportunidade"
    assert total_historico == 1  # só o avistamento inicial, alta não conta


def test_novo_anuncio_sem_preco_nao_quebra_e_nao_gera_historico(tmp_path):
    """Achado ao vivo (rodada de computador logo após max_paginas subir
    5->10): anúncio novo sem preço detectado (preco=None) fazia o INSERT
    em historico_precos violar o NOT NULL da coluna -- upsert_ads()
    inteiro estourava exceção e a rodada da categoria era descartada
    por completo, sem gravar nada, em silêncio (só log + tentativa de
    Telegram)."""
    storage = _reload_storage(tmp_path, "teste_sem_preco.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    resultado = storage.upsert_ads([_ad(1, coleta_1, preco=None)])

    assert resultado.novos == {1}
    with storage.get_connection() as conn:
        total_anuncio = conn.execute(
            "SELECT COUNT(*) FROM anuncios WHERE listing_id = 1"
        ).fetchone()[0]
        total_historico = conn.execute(
            "SELECT COUNT(*) FROM historico_precos WHERE listing_id = 1"
        ).fetchone()[0]
    assert total_anuncio == 1  # o anúncio em si é salvo normalmente (preco NULL é válido ali)
    assert total_historico == 0  # nada pra registrar como "primeiro preço"


def test_anuncio_que_some_da_rodada_fica_inativo(tmp_path):
    storage = _reload_storage(tmp_path, "teste_sumico.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(1, coleta_1), _ad(2, coleta_1)])
    storage.upsert_ads([_ad(2, coleta_2)])  # anuncio 1 nao veio nesta rodada

    with storage.get_connection() as conn:
        ativo_1 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 1").fetchone()[0]
        ativo_2 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 2").fetchone()[0]
    assert ativo_1 == 0
    assert ativo_2 == 1


def test_anuncio_que_reaparece_depois_de_sumir_conta_como_novo(tmp_path):
    storage = _reload_storage(tmp_path, "teste_reaparece.db")
    storage.init_db()

    coleta_1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
    coleta_3 = datetime(2026, 1, 1, 12, 24, 0, tzinfo=timezone.utc)

    resultado_1 = storage.upsert_ads([_ad(1, coleta_1), _ad(2, coleta_1)])
    assert resultado_1.novos == {1, 2}

    storage.upsert_ads([_ad(2, coleta_2)])  # anuncio 1 nao veio -> fica inativo
    with storage.get_connection() as conn:
        ativo_1 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 1").fetchone()[0]
    assert ativo_1 == 0

    resultado_3 = storage.upsert_ads([_ad(1, coleta_3), _ad(2, coleta_3)])
    assert 1 in resultado_3.novos  # reapareceu -> tratado como novo de novo


def test_upsert_ads_lista_vazia_nao_quebra(tmp_path):
    storage = _reload_storage(tmp_path, "teste_vazio.db")
    storage.init_db()
    resultado = storage.upsert_ads([])
    assert resultado.novos == set()
    assert resultado.quedas == {}


def test_listing_duplicado_na_mesma_rodada_nao_quebra_a_rodada_inteira(tmp_path):
    """Bug real, achado rodando o scraper contra a OLX ao vivo em
    15/09/2026: a paginação repete um anúncio (destaque reaparecendo, ou
    os resultados mudando de ordem entre a requisição de uma página e
    outra) -- `ads` chega em `upsert_ads` com o mesmo listing_id duas
    vezes na MESMA rodada. Antes do fix, isso gerava duas linhas de
    `historico_precos` com o mesmo (listing_id, plataforma,
    registrado_em) e violava a UNIQUE constraint da tabela -- como a
    exceção estourava dentro da mesma transação do upsert de `anuncios`,
    a rodada inteira era descartada (nem os anúncios sem duplicata eram
    gravados). Aqui simula 3 anúncios, um deles repetido."""
    storage = _reload_storage(tmp_path, "teste_dup_na_rodada.db")
    storage.init_db()
    agora = datetime.now(timezone.utc)

    resultado = storage.upsert_ads(
        [_ad(1, agora, preco=500.0), _ad(2, agora, preco=700.0), _ad(1, agora, preco=500.0)]
    )

    with storage.get_connection() as conn:
        total_anuncios = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
        total_historico = conn.execute(
            "SELECT COUNT(*) FROM historico_precos WHERE listing_id = 1"
        ).fetchone()[0]
    assert total_anuncios == 2
    assert total_historico == 1
    assert resultado.novos == {1, 2}


def test_contagem_media_ultimas_coletas_usa_tabela_coletas(tmp_path):
    storage = _reload_storage(tmp_path, "teste_media.db")
    storage.init_db()

    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        coleta = base.replace(minute=i * 12)
        storage.upsert_ads([_ad(1, coleta), _ad(2, coleta), _ad(3, coleta)], uf="ES")

    media = storage.contagem_media_ultimas_coletas(n=5, uf="ES")
    assert media == 3.0


def test_migracao_automatica_do_schema_legado(tmp_path):
    """O banco em produção já tem meses de dados no schema antigo (1
    linha por coleta, PK incluindo coletado_em). init_db() precisa
    migrar sozinho, na subida, sem passo manual."""
    storage = _reload_storage(tmp_path, "teste_migracao.db")

    schema_antigo = """
    CREATE TABLE anuncios (
        listing_id INTEGER NOT NULL,
        plataforma TEXT NOT NULL DEFAULT 'olx',
        titulo TEXT NOT NULL,
        preco REAL,
        preco_antigo REAL,
        url TEXT NOT NULL,
        data_publicacao TEXT NOT NULL,
        municipio TEXT, bairro TEXT, marca TEXT, condicao TEXT,
        polegadas TEXT, resolucao_max TEXT, faixa_hz TEXT, hz_exato INTEGER,
        tipo_tela TEXT, tipo_monitor TEXT, curvo INTEGER NOT NULL DEFAULT 0,
        vendedor_nome TEXT, vendedor_nota REAL,
        coletado_em TEXT NOT NULL,
        PRIMARY KEY (listing_id, plataforma, coletado_em)
    );
    """
    with storage.get_connection() as conn:
        conn.executescript(schema_antigo)
        linhas = [
            # listing 1: mesmo preco em 3 rodadas (o caso mais comum -- duplicata pura)
            (1, "olx", "Monitor A", 1000.0, None, "https://x/1", "2026-01-01T00:00:00",
             None, None, "AOC", None, None, None, None, None, None, "Monitor Gamer", 0,
             None, None, "2026-01-01T12:00:00"),
            (1, "olx", "Monitor A", 1000.0, None, "https://x/1", "2026-01-01T00:00:00",
             None, None, "AOC", None, None, None, None, None, None, "Monitor Gamer", 0,
             None, None, "2026-01-01T12:12:00"),
            (1, "olx", "Monitor A", 1000.0, None, "https://x/1", "2026-01-01T00:00:00",
             None, None, "AOC", None, None, None, None, None, None, "Monitor Gamer", 0,
             None, None, "2026-01-01T12:24:00"),
            # listing 2: cai de preco entre a 1a e a 2a rodada, some na 3a (nao tem
            # linha em 12:24:00, que e a rodada global mais recente)
            (2, "olx", "Monitor B", 900.0, None, "https://x/2", "2026-01-01T00:00:00",
             None, None, "LG", None, None, None, None, None, None, "Monitor", 0,
             None, None, "2026-01-01T12:00:00"),
            (2, "olx", "Monitor B", 700.0, None, "https://x/2", "2026-01-01T00:00:00",
             None, None, "LG", None, None, None, None, None, None, "Monitor", 0,
             None, None, "2026-01-01T12:12:00"),
        ]
        conn.executemany(
            """
            INSERT INTO anuncios (
                listing_id, plataforma, titulo, preco, preco_antigo, url,
                data_publicacao, municipio, bairro, marca, condicao,
                polegadas, resolucao_max, faixa_hz, hz_exato, tipo_tela,
                tipo_monitor, curvo, vendedor_nome, vendedor_nota, coletado_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            linhas,
        )

    storage.init_db()  # dispara a migração automática

    with storage.get_connection() as conn:
        total_novo = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
        listing_1 = conn.execute(
            "SELECT preco, ativo FROM anuncios WHERE listing_id = 1"
        ).fetchone()
        listing_2 = conn.execute(
            "SELECT preco, ativo FROM anuncios WHERE listing_id = 2"
        ).fetchone()
        quedas = conn.execute(
            "SELECT preco, preco_anterior FROM historico_precos WHERE listing_id = 2 ORDER BY registrado_em"
        ).fetchall()
        legado_preservado = conn.execute(
            "SELECT COUNT(*) FROM anuncios_legado_pre_migracao"
        ).fetchone()[0]

    assert total_novo == 2  # 4 linhas legadas -> 2 anúncios distintos
    assert listing_1 == (1000.0, 1)  # última rodada foi a global mais recente -> ativo
    assert listing_2 == (700.0, 0)  # não apareceu na última rodada global -> inativo
    assert quedas == [(900.0, None), (700.0, 900.0)]
    assert legado_preservado == 5  # nada foi apagado

    # idempotência: rodar de novo não deve duplicar nem quebrar
    storage.init_db()
    with storage.get_connection() as conn:
        total_apos_segunda_chamada = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    assert total_apos_segunda_chamada == 2


def test_eh_minimo_historico_true_no_primeiro_preco(tmp_path):
    storage = _reload_storage(tmp_path, "teste_minimo1.db")
    storage.init_db()
    storage.upsert_ads([_ad(1, datetime.now(timezone.utc), preco=500.0)])
    assert storage.eh_minimo_historico(1, 500.0) is True


def test_eh_minimo_historico_false_quando_ja_esteve_mais_barato(tmp_path):
    """'Queda' (preço menor que a rodada anterior) e 'mínimo histórico'
    (menor preço que ESSE anúncio já teve, em qualquer rodada) não são a
    mesma coisa -- um anúncio pode subir sem virar linha de histórico e
    depois cair de novo sem nunca bater o mínimo real."""
    storage = _reload_storage(tmp_path, "teste_minimo2.db")
    storage.init_db()
    c1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    c2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)
    c3 = datetime(2026, 1, 1, 12, 24, tzinfo=timezone.utc)
    storage.upsert_ads([_ad(1, c1, preco=1000.0)])
    storage.upsert_ads([_ad(1, c2, preco=800.0)])  # queda -- novo mínimo (800)
    storage.upsert_ads([_ad(1, c3, preco=950.0)])  # subiu -- não gera histórico

    # 900 seria "queda" em relação a 950, mas 900 > 800 (mínimo real já visto)
    assert storage.eh_minimo_historico(1, 900.0) is False
    assert storage.eh_minimo_historico(1, 800.0) is True
    assert storage.eh_minimo_historico(1, 700.0) is True  # abaixo de tudo que já existiu


def test_upsert_grava_categoria_e_grupo_de_iphone(tmp_path):
    storage = _reload_storage(tmp_path, "teste_iphone1.db")
    storage.init_db()
    storage.upsert_ads([_ad_iphone(1, datetime.now(timezone.utc))], uf="ES")

    with storage.get_connection() as conn:
        categoria, grupo, uf, modelo, armazenamento = conn.execute(
            "SELECT categoria, grupo, uf, modelo, armazenamento_gb FROM anuncios WHERE listing_id = 1"
        ).fetchone()
    assert categoria == "iphone"
    assert grupo == "ES · IPHONE 13 · 128GB"
    assert uf == "ES"
    assert modelo == "IPHONE 13"
    assert armazenamento == 128


def test_coleta_de_uma_categoria_nao_desativa_a_outra(tmp_path):
    """O bug que essa arquitetura precisa evitar: rodar a coleta de iPhone
    não pode marcar monitor como 'sumido' só porque nenhum monitor veio
    na lista de anúncios de iPhone daquela rodada -- e vice-versa."""
    storage = _reload_storage(tmp_path, "teste_isolamento.db")
    storage.init_db()

    c1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    c2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)
    c3 = datetime(2026, 1, 1, 12, 24, tzinfo=timezone.utc)
    storage.upsert_ads([_ad(1, c1), _ad(2, c1)])  # 2 monitores ativos
    storage.upsert_ads([_ad_iphone(101, c2)], uf="ES")  # rodada de iPhone, sem nenhum monitor

    with storage.get_connection() as conn:
        ativo_monitor_1 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 1").fetchone()[0]
        ativo_monitor_2 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 2").fetchone()[0]
        ativo_iphone = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 101").fetchone()[0]

    assert ativo_monitor_1 == 1  # não pode ter sido marcado como sumido
    assert ativo_monitor_2 == 1
    assert ativo_iphone == 1

    # e o inverso: uma rodada de monitor sem esse iPhone não pode desativá-lo
    storage.upsert_ads([_ad(1, c3), _ad(2, c3)])
    with storage.get_connection() as conn:
        ainda_ativo_iphone = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 101").fetchone()[0]
    assert ainda_ativo_iphone == 1


def test_coleta_de_uma_regiao_de_iphone_nao_desativa_outra_regiao(tmp_path):
    """Regressão direta do bug de 'sumiço' cruzado entre regiões: rodar
    iPhone de SP na mesma execução não pode marcar os iPhones do ES (região
    processada antes, no mesmo ciclo) como sumidos só porque não vieram no
    lote de SP -- mesmo risco que categoria já resolvia, agora pra UF."""
    storage = _reload_storage(tmp_path, "teste_isolamento_regiao.db")
    storage.init_db()

    c1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    c2 = datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc)  # mesmo ciclo, região seguinte

    storage.upsert_ads([_ad_iphone(1, c1, uf="ES"), _ad_iphone(2, c1, uf="ES")], uf="ES")
    storage.upsert_ads([_ad_iphone(101, c2, uf="SP")], uf="SP")  # rodada de SP, sem nenhum iPhone de ES

    with storage.get_connection() as conn:
        ativo_es_1 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 1").fetchone()[0]
        ativo_es_2 = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 2").fetchone()[0]
        ativo_sp = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 101").fetchone()[0]

    assert ativo_es_1 == 1  # não pode ter sido marcado como sumido pela rodada de SP
    assert ativo_es_2 == 1
    assert ativo_sp == 1

    # e o inverso: uma rodada de ES sem esse SP não pode desativá-lo
    c3 = datetime(2026, 1, 1, 12, 4, tzinfo=timezone.utc)
    storage.upsert_ads([_ad_iphone(1, c3, uf="ES"), _ad_iphone(2, c3, uf="ES")], uf="ES")
    with storage.get_connection() as conn:
        ainda_ativo_sp = conn.execute("SELECT ativo FROM anuncios WHERE listing_id = 101").fetchone()[0]
    assert ainda_ativo_sp == 1


def test_mediana_de_uma_categoria_nao_mistura_com_a_outra(tmp_path):
    storage = _reload_storage(tmp_path, "teste_isolamento2.db")
    storage.init_db()
    agora = datetime.now(timezone.utc)

    monitores = [_ad(i, agora, preco=p) for i, p in enumerate([300, 400, 500, 600, 700], start=1)]
    iphones = [_ad_iphone(i, agora, preco=p) for i, p in enumerate([2000, 2100, 2200, 2300, 2400], start=101)]
    storage.upsert_ads(monitores)
    storage.upsert_ads(iphones, uf="ES")

    from common.stats import medianas_todos_grupos
    medianas = medianas_todos_grupos()
    assert medianas[("monitor", "AOC · Monitor Gamer")] == 500.0
    assert medianas[("iphone", "ES · IPHONE 13 · 128GB")] == 2200.0


def test_mediana_de_iphone_nao_mistura_estados_diferentes(tmp_path):
    """A prova central da arquitetura multi-região: mesmo modelo lógico
    (IPHONE 13 · 128GB) em dois estados não pode compartilhar mediana --
    um preço de SP não pode inflar/afundar a decisão de oportunidade de um
    anúncio do ES, e vice-versa. `IphoneAd.grupo` já embute a UF (ver
    common/schema.py) -- este teste prova que isso realmente isola as duas
    medianas, não só que o código roda sem erro."""
    storage = _reload_storage(tmp_path, "teste_multi_regiao_mediana.db")
    storage.init_db()
    # coletado_em diferente entre as duas regiões -- reflete a realidade de
    # produção (rodadas sequenciais com delay real entre elas, nunca no
    # mesmo instante) e evita colidir na PK de `coletas`
    # (coletado_em, plataforma, categoria) -- ver
    # test_contagem_media_ultimas_coletas_separa_por_uf pro mesmo cuidado.
    c_es = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    c_sp = datetime(2026, 1, 1, 12, 0, 3, tzinfo=timezone.utc)

    es = [_ad_iphone(i, c_es, preco=p, uf="ES") for i, p in enumerate([2000, 2100, 2200, 2300, 2400], start=1)]
    sp = [_ad_iphone(i, c_sp, preco=p, uf="SP") for i, p in enumerate([4000, 4100, 4200, 4300, 4400], start=101)]
    storage.upsert_ads(es, uf="ES")
    storage.upsert_ads(sp, uf="SP")

    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM anuncios WHERE ativo = 1").fetchone()[0]
    assert total == 10  # as duas regiões coexistem, nenhuma marcou a outra como sumida

    from common.stats import medianas_todos_grupos
    medianas = medianas_todos_grupos()
    assert medianas[("iphone", "ES · IPHONE 13 · 128GB")] == 2200.0
    assert medianas[("iphone", "SP · IPHONE 13 · 128GB")] == 4200.0


def test_contagem_media_ultimas_coletas_separa_por_categoria(tmp_path):
    storage = _reload_storage(tmp_path, "teste_contagem_categoria.db")
    storage.init_db()
    c1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    c2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(i, c1) for i in range(1, 4)], uf="ES")  # 3 monitores
    storage.upsert_ads([_ad_iphone(i, c1) for i in range(101, 111)], uf="ES")  # 10 iphones
    storage.upsert_ads([_ad(i, c2) for i in range(1, 4)], uf="ES")
    storage.upsert_ads([_ad_iphone(i, c2) for i in range(101, 111)], uf="ES")

    assert storage.contagem_media_ultimas_coletas(categoria="monitor", uf="ES") == 3.0
    assert storage.contagem_media_ultimas_coletas(categoria="iphone", uf="ES") == 10.0


def test_contagem_media_ultimas_coletas_separa_por_uf(tmp_path):
    """Regressão direta do bug de falso alarme de sanidade: uma região
    pequena (ES, 2 anúncios/rodada) não pode ser comparada contra a média
    de uma região grande (SP, 20 anúncios/rodada) só porque as duas são
    'iphone' -- cada `uf` precisa da própria média histórica."""
    storage = _reload_storage(tmp_path, "teste_contagem_uf.db")
    storage.init_db()
    # coletado_em diferente entre ES e SP dentro do mesmo ciclo -- reflete
    # o delay real entre regiões em produção, e evita colidir na PK de
    # `coletas` (coletado_em, plataforma, categoria): as duas são 'iphone'.
    c1_es = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    c1_sp = datetime(2026, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
    c2_es = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
    c2_sp = datetime(2026, 1, 1, 12, 12, 3, tzinfo=timezone.utc)

    storage.upsert_ads([_ad_iphone(i, c1_es, uf="ES") for i in range(1, 3)], uf="ES")  # 2 anúncios
    storage.upsert_ads([_ad_iphone(i, c1_sp, uf="SP") for i in range(101, 121)], uf="SP")  # 20 anúncios
    storage.upsert_ads([_ad_iphone(i, c2_es, uf="ES") for i in range(1, 3)], uf="ES")
    storage.upsert_ads([_ad_iphone(i, c2_sp, uf="SP") for i in range(101, 121)], uf="SP")

    assert storage.contagem_media_ultimas_coletas(categoria="iphone", uf="ES") == 2.0
    assert storage.contagem_media_ultimas_coletas(categoria="iphone", uf="SP") == 20.0


def test_migracao_adiciona_categoria_e_grupo_em_banco_pre_multi_categoria(tmp_path):
    """Simula o estado real do banco em produção: já no schema '1 linha
    por anúncio' (migração anterior já rodou) mas de antes de existir
    categoria/grupo -- só monitor. init_db() precisa adicionar as colunas
    sozinho, sem apagar nada."""
    storage = _reload_storage(tmp_path, "teste_migracao_categoria.db")
    agora = datetime.now(timezone.utc)
    storage.init_db()
    storage.upsert_ads([_ad(1, agora, preco=500.0), _ad(2, agora, preco=700.0)])

    # simula "banco de antes de categoria existir": remove as colunas
    # recriando a tabela do jeito antigo e copiando os dados de volta.
    with storage.get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE anuncios_sem_categoria (
                listing_id INTEGER NOT NULL,
                plataforma TEXT NOT NULL DEFAULT 'olx',
                titulo TEXT NOT NULL,
                preco REAL,
                preco_antigo REAL,
                url TEXT NOT NULL,
                data_publicacao TEXT NOT NULL,
                municipio TEXT, bairro TEXT, marca TEXT, condicao TEXT,
                polegadas TEXT, resolucao_max TEXT, faixa_hz TEXT, hz_exato INTEGER,
                tipo_tela TEXT, tipo_monitor TEXT, curvo INTEGER NOT NULL DEFAULT 0,
                vendedor_nome TEXT, vendedor_nota REAL,
                primeiro_visto_em TEXT NOT NULL, ultimo_visto_em TEXT NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1, removido_em TEXT,
                PRIMARY KEY (listing_id, plataforma)
            );
            INSERT INTO anuncios_sem_categoria
                (listing_id, plataforma, titulo, preco, preco_antigo, url, data_publicacao,
                 municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz,
                 hz_exato, tipo_tela, tipo_monitor, curvo, vendedor_nome, vendedor_nota,
                 primeiro_visto_em, ultimo_visto_em, ativo, removido_em)
            SELECT listing_id, plataforma, titulo, preco, preco_antigo, url, data_publicacao,
                   municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz,
                   hz_exato, tipo_tela, tipo_monitor, curvo, vendedor_nome, vendedor_nota,
                   primeiro_visto_em, ultimo_visto_em, ativo, removido_em
            FROM anuncios;
            DROP TABLE anuncios;
            ALTER TABLE anuncios_sem_categoria RENAME TO anuncios;
            """
        )

    storage.init_db()  # dispara _adiciona_multi_categoria_se_necessario

    with storage.get_connection() as conn:
        linha_1 = conn.execute(
            "SELECT categoria, grupo, preco FROM anuncios WHERE listing_id = 1"
        ).fetchone()

    assert linha_1 == ("monitor", "AOC · Monitor Gamer", 500.0)

    # idempotência
    storage.init_db()
    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    assert total == 2


def test_migracao_adiciona_uf_e_corrige_grupo_de_iphone_legado(tmp_path):
    """Simula o estado do banco de antes da região (uf) existir -- inclusive
    um iPhone já salvo com `grupo` no formato antigo (sem prefixo de UF,
    de antes de IphoneAd.grupo passar a incluir a região). init_db()
    precisa adicionar a coluna `uf` (backfill 'ES', único estado que já
    rodou) E corrigir o grupo do iPhone legado pro formato novo -- sem
    tocar no grupo de monitor, que nunca leva UF."""
    storage = _reload_storage(tmp_path, "teste_migracao_uf.db")
    agora = datetime.now(timezone.utc)
    storage.init_db()
    storage.upsert_ads([_ad(1, agora, preco=500.0)])  # monitor
    storage.upsert_ads([_ad_iphone(2, agora, preco=1000.0)], uf="ES")  # iphone

    with storage.get_connection() as conn:
        # força o grupo do iPhone de volta pro formato ANTIGO (sem UF) --
        # é o estado real que uma instalação em produção teria antes desta
        # migração existir.
        conn.execute("UPDATE anuncios SET grupo = 'IPHONE 13 · 128GB' WHERE listing_id = 2")
        # recria a tabela sem a coluna uf, mesmo truque de
        # test_migracao_adiciona_categoria_e_grupo_em_banco_pre_multi_categoria,
        # pra simular um banco de antes dela existir.
        conn.executescript(
            """
            CREATE TABLE anuncios_sem_uf (
                listing_id INTEGER NOT NULL,
                plataforma TEXT NOT NULL DEFAULT 'olx',
                categoria TEXT NOT NULL DEFAULT 'monitor',
                grupo TEXT NOT NULL DEFAULT '',
                titulo TEXT NOT NULL,
                preco REAL, preco_antigo REAL, url TEXT NOT NULL, data_publicacao TEXT NOT NULL,
                municipio TEXT, bairro TEXT, marca TEXT, condicao TEXT,
                polegadas TEXT, resolucao_max TEXT, faixa_hz TEXT, hz_exato INTEGER,
                tipo_tela TEXT, tipo_monitor TEXT, curvo INTEGER NOT NULL DEFAULT 0,
                modelo TEXT, armazenamento_gb INTEGER, cor TEXT, saude_bateria TEXT,
                cpu_marca TEXT, cpu_modelo TEXT, ram_gb INTEGER, inclui_monitor INTEGER NOT NULL DEFAULT 0,
                vendedor_nome TEXT, vendedor_nota REAL,
                primeiro_visto_em TEXT NOT NULL, ultimo_visto_em TEXT NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1, removido_em TEXT,
                PRIMARY KEY (listing_id, plataforma)
            );
            INSERT INTO anuncios_sem_uf
                (listing_id, plataforma, categoria, grupo, titulo, preco, preco_antigo, url, data_publicacao,
                 municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz, hz_exato,
                 tipo_tela, tipo_monitor, curvo, modelo, armazenamento_gb, cor, saude_bateria,
                 cpu_marca, cpu_modelo, ram_gb, inclui_monitor, vendedor_nome, vendedor_nota,
                 primeiro_visto_em, ultimo_visto_em, ativo, removido_em)
            SELECT listing_id, plataforma, categoria, grupo, titulo, preco, preco_antigo, url, data_publicacao,
                   municipio, bairro, marca, condicao, polegadas, resolucao_max, faixa_hz, hz_exato,
                   tipo_tela, tipo_monitor, curvo, modelo, armazenamento_gb, cor, saude_bateria,
                   cpu_marca, cpu_modelo, ram_gb, inclui_monitor, vendedor_nome, vendedor_nota,
                   primeiro_visto_em, ultimo_visto_em, ativo, removido_em
            FROM anuncios;
            DROP TABLE anuncios;
            ALTER TABLE anuncios_sem_uf RENAME TO anuncios;
            """
        )

    storage.init_db()  # dispara _adiciona_uf_se_necessario

    with storage.get_connection() as conn:
        monitor_row = conn.execute("SELECT grupo, uf FROM anuncios WHERE listing_id = 1").fetchone()
        iphone_row = conn.execute("SELECT grupo, uf FROM anuncios WHERE listing_id = 2").fetchone()

    assert monitor_row == ("AOC · Monitor Gamer", "ES")  # só ganha a coluna uf, grupo intacto (sem UF)
    assert iphone_row == ("ES · IPHONE 13 · 128GB", "ES")  # grupo corrigido pro formato novo + coluna uf

    # idempotência: rodar de novo não duplica o prefixo "ES · "
    storage.init_db()
    with storage.get_connection() as conn:
        grupo_apos_segunda_chamada = conn.execute(
            "SELECT grupo FROM anuncios WHERE listing_id = 2"
        ).fetchone()[0]
    assert grupo_apos_segunda_chamada == "ES · IPHONE 13 · 128GB"


def test_upsert_grava_campos_de_computador(tmp_path):
    storage = _reload_storage(tmp_path, "teste_pc1.db")
    storage.init_db()
    storage.upsert_ads([_ad_computador(1, datetime.now(timezone.utc))])

    with storage.get_connection() as conn:
        categoria, grupo, cpu, ram = conn.execute(
            "SELECT categoria, grupo, cpu_modelo, ram_gb FROM anuncios WHERE listing_id = 1"
        ).fetchone()
    assert categoria == "computador"
    assert grupo == "Intel Core i5 · 8GB RAM"
    assert cpu == "Intel Core i5"
    assert ram == 8


def test_tres_categorias_nao_interferem_entre_si(tmp_path):
    """Extensão do teste de isolamento pra 3 categorias -- cada uma tem
    que poder rodar sem afetar o 'ativo' das outras duas."""
    storage = _reload_storage(tmp_path, "teste_isolamento3.db")
    storage.init_db()

    c1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    c2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)
    c3 = datetime(2026, 1, 1, 12, 24, tzinfo=timezone.utc)

    storage.upsert_ads([_ad(1, c1)])
    storage.upsert_ads([_ad_iphone(101, c1)], uf="ES")
    storage.upsert_ads([_ad_computador(201, c1)])

    # rodadas seguintes de cada categoria, sem as outras duas
    storage.upsert_ads([_ad(1, c2)])
    storage.upsert_ads([_ad_iphone(101, c2)], uf="ES")
    storage.upsert_ads([_ad_computador(201, c3)])

    with storage.get_connection() as conn:
        ativos = {
            lid: conn.execute("SELECT ativo FROM anuncios WHERE listing_id = ?", (lid,)).fetchone()[0]
            for lid in (1, 101, 201)
        }
    assert ativos == {1: 1, 101: 1, 201: 1}

    from common.stats import medianas_todos_grupos
    assert set(c for c, g in medianas_todos_grupos(categoria=None).keys()) <= {"monitor", "iphone", "computador"}

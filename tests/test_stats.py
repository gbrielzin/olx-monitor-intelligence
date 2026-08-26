from datetime import datetime, timezone

from common.config import settings
from common.schema import MonitorAd


def _reload_storage(tmp_path, nome: str):
    settings.db_path = str(tmp_path / nome)
    from common import storage
    import importlib

    importlib.reload(storage)
    return storage


def _ad(listing_id: int, preco: float, marca: str = "AOC", tipo: str = "Monitor Gamer", coletado_em=None):
    return MonitorAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"anuncio {listing_id}",
            "url": f"https://x/{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "marca": marca,
            "tipo_monitor": tipo,
            "coletado_em": coletado_em or datetime.now(timezone.utc),
        }
    )


def test_preco_mediano_grupo_none_com_amostra_insuficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t1.db")
    storage.init_db()
    storage.upsert_ads([_ad(1, 100.0), _ad(2, 200.0)])  # só 2, mínimo configurado é 5

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("AOC", "Monitor Gamer") is None


def test_preco_mediano_grupo_calcula_com_amostra_suficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t2.db")
    storage.init_db()
    precos = [100.0, 200.0, 300.0, 400.0, 500.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("AOC", "Monitor Gamer") == 300.0


def test_preco_mediano_grupo_ignora_anuncio_inativo(tmp_path):
    storage = _reload_storage(tmp_path, "t3.db")
    storage.init_db()
    coleta_1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)
    precos = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]
    storage.upsert_ads([_ad(i, p, coletado_em=coleta_1) for i, p in enumerate(precos, start=1)])
    # rodada seguinte sem o listing 6 (preço 600) -- fica inativo
    storage.upsert_ads([_ad(i, p, coletado_em=coleta_2) for i, p in enumerate(precos[:5], start=1)])

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("AOC", "Monitor Gamer") == 300.0  # não 350 (incluiria o 600 inativo)


def test_medianas_todos_grupos_calcula_todos_de_uma_vez(tmp_path):
    storage = _reload_storage(tmp_path, "t4.db")
    storage.init_db()
    ads = (
        [_ad(i, p, marca="AOC", tipo="Monitor Gamer") for i, p in enumerate([100, 200, 300, 400, 500], start=1)]
        + [_ad(i, p, marca="LG", tipo="Monitor") for i, p in enumerate([600, 700, 800, 900, 1000], start=101)]
        + [_ad(201, 50.0, marca="Dell", tipo="Monitor")]  # só 1 -- abaixo da amostra mínima
    )
    storage.upsert_ads(ads)

    from common.stats import medianas_todos_grupos
    medianas = medianas_todos_grupos()
    assert medianas[("AOC", "Monitor Gamer")] == 300.0
    assert medianas[("LG", "Monitor")] == 800.0
    assert ("Dell", "Monitor") not in medianas


def test_avaliar_preco_calcula_custo_e_margem_a_partir_da_negociacao_esperada():
    from common.stats import avaliar_preco

    av = avaliar_preco(preco=800.0, mediana=1000.0)
    custo_esperado = 800.0 * (1 - settings.desconto_negociacao_esperado)
    assert av.custo_apos_negociacao == custo_esperado
    assert av.margem_rs == 1000.0 - custo_esperado
    assert av.margem_pct == (1000.0 - custo_esperado) / custo_esperado


def test_avaliar_preco_marca_oportunidade_conforme_limiar():
    from common.stats import avaliar_preco

    limiar = settings.oportunidade_limiar
    mediana = 1000.0
    assert avaliar_preco(preco=mediana * limiar, mediana=mediana).eh_oportunidade is True
    assert avaliar_preco(preco=mediana * limiar + 1, mediana=mediana).eh_oportunidade is False


def test_avaliar_none_sem_preco(tmp_path):
    storage = _reload_storage(tmp_path, "t5.db")
    storage.init_db()

    from common.stats import avaliar
    assert avaliar(None, "AOC", "Monitor Gamer") is None


def test_avaliar_none_com_amostra_insuficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t6.db")
    storage.init_db()

    from common.stats import avaliar
    assert avaliar(500.0, "MarcaQueNaoExiste", "Monitor") is None


def test_avaliar_retorna_avaliacao_completa(tmp_path):
    storage = _reload_storage(tmp_path, "t7.db")
    storage.init_db()
    precos = [100.0, 200.0, 300.0, 400.0, 500.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import avaliar
    av = avaliar(200.0, "AOC", "Monitor Gamer")
    assert av is not None
    assert av.mediana == 300.0
    assert av.preco == 200.0

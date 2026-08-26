from common.schema import MonitorAd
from scraper.sanity import checar_sanidade


def _ad(preco=100.0):
    return MonitorAd.model_validate(
        {
            "listing_id": 1,
            "titulo": "x",
            "url": "https://x",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
        }
    )


def test_zero_anuncios_falha():
    assert not checar_sanidade([], None).ok


def test_maioria_sem_preco_falha():
    ads = [_ad(preco=None) for _ in range(8)] + [_ad(preco=100.0) for _ in range(2)]
    assert not checar_sanidade(ads, None).ok


def test_queda_abrupta_vs_media_historica_falha():
    ads = [_ad() for _ in range(3)]
    assert not checar_sanidade(ads, media_historica=40.0).ok


def test_coleta_normal_passa():
    ads = [_ad() for _ in range(20)]
    assert checar_sanidade(ads, media_historica=18.0).ok

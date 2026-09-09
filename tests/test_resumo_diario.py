from datetime import datetime, timezone

from common.config import settings
from common.schema import MonitorAd
from scraper import resumo_diario


def _reload_storage(tmp_path, nome: str):
    settings.db_path = str(tmp_path / nome)
    from common import storage
    import importlib

    importlib.reload(storage)
    storage.init_db()
    return storage


def _ad(listing_id: int, preco: float, marca: str = "AOC", condicao: str = "Usado - Bom"):
    return MonitorAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"Monitor {marca} {listing_id}",
            "url": f"https://x/{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "marca": marca,
            "tipo_monitor": "Monitor Gamer",
            "condicao": condicao,
            "coletado_em": datetime.now(timezone.utc),
        }
    )


def test_desativado_por_padrao_nao_chama_api():
    settings.resumo_diario_ativo = False
    resumo_diario.enviar_resumo_diario()  # não deve levantar nem precisar de rede/API key


def test_ativado_sem_api_key_nao_quebra():
    settings.resumo_diario_ativo = True
    settings.anthropic_api_key = ""
    try:
        resumo_diario.enviar_resumo_diario()  # sem chave configurada, só loga e retorna
    finally:
        settings.resumo_diario_ativo = False


def test_panorama_encontra_oportunidade_e_ordena_por_margem(tmp_path):
    _reload_storage(tmp_path, "resumo.db")
    # amostra mínima (5) com preço alto estabelece a mediana do grupo;
    # os dois últimos, bem abaixo dela, viram oportunidade.
    ads = [_ad(i, preco=1000.0) for i in range(1, 6)]
    ads.append(_ad(6, preco=600.0))
    ads.append(_ad(7, preco=500.0))  # margem maior -- deve vir primeiro
    from common.storage import upsert_ads

    upsert_ads(ads)

    panorama = resumo_diario._panorama()
    dados = panorama["monitor"]
    assert dados["ativos"] == 7
    assert dados["total_oportunidades"] == 2
    assert dados["oportunidades"][0]["preco"] == 500.0  # maior margem primeiro


def test_panorama_categoria_sem_dado_nenhum_nao_quebra(tmp_path):
    _reload_storage(tmp_path, "resumo_vazio.db")
    panorama = resumo_diario._panorama()
    assert panorama["monitor"]["ativos"] == 0
    assert panorama["monitor"]["total_oportunidades"] == 0
    assert panorama["monitor"]["oportunidades"] == []

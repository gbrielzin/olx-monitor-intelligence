from datetime import datetime, timezone

from common.config import settings
from common.schema import IphoneAd
from scraper import resumo_diario


def _reload_storage(tmp_path, nome: str):
    settings.db_path = str(tmp_path / nome)
    from common import storage
    import importlib

    importlib.reload(storage)
    storage.init_db()
    return storage


def _ad_iphone(
    listing_id: int, preco: float, uf: str = "ES",
    modelo: str = "IPHONE 13", armazenamento_gb: int = 128, condicao: str = "Usado - Bom",
):
    return IphoneAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": f"iPhone {modelo} {listing_id}",
            "url": f"https://x/{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "modelo": modelo,
            "armazenamento_gb": armazenamento_gb,
            "uf": uf,
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
    # amostra mínima (5) com preço alto estabelece a mediana do grupo; os
    # dois últimos, bem abaixo dela, viram oportunidade -- ambos acima do
    # piso de iPhone (orcamento_minimo_iphone=600 em common/config.py, bem
    # maior que o de monitor usado no teste original) pra não serem
    # descartados como "preço bom demais pra ser real".
    ads = [_ad_iphone(i, preco=1000.0) for i in range(1, 6)]
    ads.append(_ad_iphone(6, preco=700.0))
    ads.append(_ad_iphone(7, preco=650.0))  # margem maior -- deve vir primeiro
    from common.storage import upsert_ads

    upsert_ads(ads, uf="ES")

    panorama = resumo_diario._panorama()
    dados = panorama["iphone"]
    assert dados["ativos"] == 7
    assert dados["total_oportunidades"] == 2
    assert dados["oportunidades"][0]["preco"] == 650.0  # maior margem primeiro


def test_panorama_categoria_sem_dado_nenhum_nao_quebra(tmp_path):
    _reload_storage(tmp_path, "resumo_vazio.db")
    panorama = resumo_diario._panorama()
    assert panorama["iphone"]["ativos"] == 0
    assert panorama["iphone"]["total_oportunidades"] == 0
    assert panorama["iphone"]["oportunidades"] == []


def test_panorama_combina_regioes_sem_misturar_mediana(tmp_path):
    """O resumo diário junta todas as regiões num panorama só (ver
    docstring de resumo_diario._panorama) -- prova que isso não faz uma
    região "vazar" preço pra mediana de comparação da outra: ES e SP têm
    níveis de preço bem diferentes, e cada um só deve gerar oportunidade
    comparado à sua própria mediana (o `grupo` já vem prefixado com a UF,
    ver common/schema.py). Preços escolhidos dentro da faixa de orçamento
    de iPhone (600-1500 em common/config.py) pra nenhum ser descartado por
    "preço bom demais"/"fora do orçamento" antes de chegar no que este
    teste quer provar."""
    from common.storage import upsert_ads

    _reload_storage(tmp_path, "resumo_multi_regiao.db")
    es = [_ad_iphone(i, preco=p, uf="ES") for i, p in enumerate([1000.0] * 4 + [650.0], start=1)]
    sp = [_ad_iphone(i, preco=p, uf="SP") for i, p in enumerate([1400.0] * 4 + [1000.0], start=101)]
    upsert_ads(es, uf="ES")
    upsert_ads(sp, uf="SP")

    panorama = resumo_diario._panorama()
    dados = panorama["iphone"]
    assert dados["ativos"] == 10
    assert dados["total_oportunidades"] == 2  # 1 por região, cada uma comparada só à própria mediana
    precos_oportunidade = {o["preco"] for o in dados["oportunidades"]}
    assert precos_oportunidade == {650.0, 1000.0}

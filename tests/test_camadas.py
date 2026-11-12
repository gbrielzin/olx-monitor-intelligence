from datetime import datetime, timezone

import pytest

from pipeline.armazenamento import ArmazenamentoLocal
from pipeline.camadas import (
    bronze_para_prata,
    chave_bronze,
    chave_prata_de,
    gravar_bronze,
    gravar_prata,
    ler_json,
    ler_prata,
)

MOMENTO = datetime(2026, 11, 12, 8, 17, 46, 876035, tzinfo=timezone.utc)


def _item(listing_id, preco="R$ 2.500", titulo="iPhone 13 128GB", modelo="IPHONE 13", condicao="Usado - Excelente"):
    return {
        "listId": listing_id,
        "subject": titulo,
        "priceValue": preco,
        "url": f"https://es.olx.com.br/x-{listing_id}",
        "date": 1787000000,
        "locationDetails": {"municipality": "Vitória", "neighbourhood": "Centro", "uf": "ES"},
        "properties": [
            {"name": "electronics_model", "value": modelo},
            {"name": "cellphone_storage", "value": "128GB"},
            {"name": "electronics_condition", "value": condicao},
        ],
        "olxPay": {"transactionalSellerName": "Fulano de Tal", "transactionalSellerRating": 4.8},
    }


def _payload(itens):
    return {
        "metadados": {"categoria": "iphone", "uf": "ES", "coletado_em": MOMENTO.isoformat()},
        "itens": itens,
    }


def test_chave_bronze_particionada_por_uf_e_data_em_utc():
    assert chave_bronze("iphone", "es", MOMENTO) == (
        "bronze/olx/iphone/uf=ES/data=2026-11-12/coleta_20261112T081746876035Z.json"
    )


def test_chave_prata_espelha_bronze():
    chave = chave_bronze("iphone", "ES", MOMENTO)
    assert chave_prata_de(chave) == "prata/olx/iphone/uf=ES/data=2026-11-12/coleta_20261112T081746876035Z.jsonl"
    with pytest.raises(ValueError):
        chave_prata_de("prata/x.jsonl")


def test_bronze_guarda_o_bruto_intacto_com_metadados(tmp_path):
    arm = ArmazenamentoLocal(tmp_path)
    itens = [{"advertisingId": "banner"}, _item(1)]
    chave = gravar_bronze(arm, itens, categoria="iphone", uf="ES", coletado_em=MOMENTO, urls=["u1"])

    payload = ler_json(arm, chave)
    assert payload["itens"] == itens  # nada filtrado nem alterado na bronze
    assert payload["metadados"]["total_itens"] == 2
    assert payload["metadados"]["total_com_listid"] == 1
    assert payload["metadados"]["urls"] == ["u1"]
    assert payload["metadados"]["coletado_em"] == MOMENTO.isoformat()


def test_quarentena_vai_pra_outro_prefixo(tmp_path):
    arm = ArmazenamentoLocal(tmp_path)
    chave = gravar_bronze(arm, [], categoria="iphone", uf="ES", coletado_em=MOMENTO, urls=[], camada="quarentena")
    assert chave.startswith("quarentena/")
    assert arm.listar("bronze/") == []


def test_prata_descarta_propaganda_invalido_e_duplicata():
    itens = [
        {"advertisingId": "banner"},
        _item(1),
        _item(1),  # paginação repetiu o anúncio
        {"listId": 2, "subject": "sem url nem data"},  # schema rejeita
        _item(3, preco="R$ 3.000"),
    ]
    linhas = bronze_para_prata(_payload(itens))
    assert [ln["listing_id"] for ln in linhas] == [1, 3]
    assert linhas[0]["preco"] == 2500.0
    assert linhas[0]["modelo"] == "IPHONE 13"
    assert linhas[0]["armazenamento_gb"] == 128
    assert linhas[0]["coleta_em"] == MOMENTO.isoformat()  # vem dos metadados, não do relógio


def test_prata_nao_carrega_dado_pessoal_do_vendedor():
    linha = bronze_para_prata(_payload([_item(1)]))[0]
    assert "vendedor_nome" not in linha
    assert "Fulano" not in str(linha)


def test_prata_marca_confiavel_com_a_mesma_regua_da_mediana():
    linhas = bronze_para_prata(_payload([
        _item(1),
        _item(2, titulo="iPhone 13 128GB tela trincada"),  # defeito no título
        _item(3, preco="R$ 50"),  # abaixo do piso de iPhone
        _item(4, condicao="Com defeito ou avarias"),
        _item(5, titulo="iPhone 15 Pro 128GB"),  # título contradiz o modelo (13)
    ]))
    assert {ln["listing_id"]: ln["confiavel"] for ln in linhas} == {1: True, 2: False, 3: False, 4: False, 5: False}


def test_prata_e_deterministica_e_round_trip(tmp_path):
    arm = ArmazenamentoLocal(tmp_path)
    chave = gravar_bronze(arm, [_item(1), _item(2)], categoria="iphone", uf="ES", coletado_em=MOMENTO, urls=[])
    payload = ler_json(arm, chave)
    primeira = bronze_para_prata(payload)
    assert bronze_para_prata(payload) == primeira

    chave_prata = gravar_prata(arm, chave, primeira)
    assert ler_prata(arm, chave_prata) == primeira

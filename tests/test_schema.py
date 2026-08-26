from common.schema import MonitorAd


def _base(**overrides):
    dados = {
        "listing_id": 1,
        "titulo": "x",
        "url": "https://x",
        "data_publicacao": "2026-01-01T00:00:00",
    }
    dados.update(overrides)
    return MonitorAd.model_validate(dados)


def test_limpa_preco_formato_moeda():
    assert _base(preco="R$ 1.250").preco == 1250.0


def test_preco_ausente_vira_none_nao_zero():
    assert _base().preco is None


def test_from_olx_json_extrai_hz_exato_do_titulo():
    raw = {
        "listId": 99,
        "subject": "Monitor Gamer 27 pol. QHD 165Hz Pichau IPS",
        "priceValue": "R$ 1.299",
        "url": "https://x/99",
        "date": 1787000000,
        "properties": [
            {"name": "info_monitors_refresh_rate", "value": "144 Hz ou maior"},
            {"name": "info_monitors_features", "value": "Curvo, Inclui cabos"},
        ],
    }
    ad = MonitorAd.from_olx_json(raw)
    assert ad.hz_exato == 165
    assert ad.faixa_hz == "144 Hz ou maior"
    assert ad.curvo is True


def test_fallback_hz_usa_faixa_estruturada_quando_titulo_nao_menciona():
    raw = {
        "listId": 100,
        "subject": "Monitor Dell 24 polegadas, estado de novo",  # sem Hz no título
        "url": "https://x/100",
        "date": 1787000000,
        "properties": [{"name": "info_monitors_refresh_rate", "value": "100 Hz"}],
    }
    assert MonitorAd.from_olx_json(raw).hz_exato == 100


def test_fallback_hz_nao_inventa_numero_pra_faixa_aberta():
    raw = {
        "listId": 101,
        "subject": "Monitor Gamer sem Hz no título",
        "url": "https://x/101",
        "date": 1787000000,
        "properties": [{"name": "info_monitors_refresh_rate", "value": "144 Hz ou maior"}],
    }
    # "144 Hz ou maior" é faixa aberta, não valor exato -- não confunde com 144.
    assert MonitorAd.from_olx_json(raw).hz_exato is None


def test_marca_outros_e_recuperada_do_titulo():
    raw = {
        "listId": 102,
        "subject": "Monitor Samsung 24 polegadas curvo, seminovo",
        "url": "https://x/102",
        "date": 1787000000,
        "properties": [{"name": "info_monitors_brand", "value": "Outros"}],
    }
    assert MonitorAd.from_olx_json(raw).marca == "Samsung"


def test_marca_ausente_tambem_e_recuperada_do_titulo():
    raw = {
        "listId": 103,
        "subject": "Monitor LG Ultrawide 29 polegadas",
        "url": "https://x/103",
        "date": 1787000000,
        "properties": [],
    }
    assert MonitorAd.from_olx_json(raw).marca == "LG"


def test_marca_estruturada_tem_prioridade_sobre_o_titulo():
    raw = {
        "listId": 104,
        "subject": "Monitor gamer bom estado, aceito troca por Dell",
        "url": "https://x/104",
        "date": 1787000000,
        "properties": [{"name": "info_monitors_brand", "value": "AOC"}],
    }
    # o título menciona "Dell" de passagem, mas o campo estruturado já veio
    # preenchido com uma marca de verdade -- esse continua sendo o dado bom.
    assert MonitorAd.from_olx_json(raw).marca == "AOC"


def test_marca_outros_sem_marca_no_titulo_permanece_outros():
    raw = {
        "listId": 105,
        "subject": "Monitor usado funcionando bem",
        "url": "https://x/105",
        "date": 1787000000,
        "properties": [{"name": "info_monitors_brand", "value": "Outros"}],
    }
    assert MonitorAd.from_olx_json(raw).marca == "Outros"


def test_marca_recuperada_e_case_insensitive():
    raw = {
        "listId": 106,
        "subject": "Monitor philips 24 polegadas",
        "url": "https://x/106",
        "date": 1787000000,
        "properties": [],
    }
    assert MonitorAd.from_olx_json(raw).marca == "Philips"


def test_marca_curta_nao_casa_no_meio_de_outra_palavra():
    # "algo" contém "lg" como substring -- sem borda de palavra (\b), um
    # regex ingênuo casaria "LG" aqui por engano.
    raw = {
        "listId": 107,
        "subject": "Monitor antigo, algo desgastado, sem marca aparente",
        "url": "https://x/107",
        "date": 1787000000,
        "properties": [],
    }
    assert MonitorAd.from_olx_json(raw).marca is None

from common.schema import ComputadorAd, IphoneAd, MonitorAd


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


# --- IphoneAd -- estrutura real da OLX (categoria "Celulares e
# Smartphones", id 3060), inspecionada ao vivo antes de escrever o parser.

def _raw_iphone(**overrides):
    props = {
        "electronics_brand": "APPLE",
        "electronics_model": "IPHONE 11",
        "electronics_condition": "Usado - Excelente",
        "cellphone_storage": "128GB",
        "electronics_color": "Preto",
        "electronics_battery_health": "Boa (80% até 94%)",
    }
    props.update(overrides.pop("props", {}))
    raw = {
        "listId": 1529533240,
        "subject": "Vendo IPhone 11 , ou troco por um 11 pro Max",
        "priceValue": "R$ 1.200",
        "oldPrice": None,
        "url": "https://es.olx.com.br/x-1529533240",
        "date": 1787313137,
        "locationDetails": {"municipality": "Cariacica", "neighbourhood": "São Conrado", "uf": "ES"},
        "olxPay": {"transactionalSellerName": "vitoria", "transactionalSellerRating": None},
        "properties": [{"name": k, "value": v} for k, v in props.items()],
    }
    raw.update(overrides)
    return raw


def test_iphone_from_olx_json_le_specs_reais():
    ad = IphoneAd.from_olx_json(_raw_iphone())
    assert ad.categoria == "iphone"
    assert ad.marca == "APPLE"
    assert ad.modelo == "IPHONE 11"
    assert ad.armazenamento_gb == 128
    assert ad.cor == "Preto"
    assert ad.condicao == "Usado - Excelente"
    assert ad.saude_bateria == "Boa (80% até 94%)"
    assert ad.preco == 1200.0
    assert ad.municipio == "Cariacica"


def test_iphone_grupo_combina_modelo_e_armazenamento():
    ad = IphoneAd.from_olx_json(_raw_iphone())
    assert ad.grupo == "IPHONE 11 · 128GB"


def test_iphone_armazenamento_em_tb_normaliza_pra_gb():
    ad = IphoneAd.from_olx_json(_raw_iphone(props={"cellphone_storage": "1TB"}))
    assert ad.armazenamento_gb == 1024


def test_iphone_modelo_invalido_cai_pro_titulo():
    """Achado nos dados reais: 'electronics_model' às vezes vem lixo tipo
    '2' ou '25' sem sentido -- nesse caso o título é a fonte confiável."""
    raw = _raw_iphone(
        subject="iPhone 13 Pro Max 256GB lacrado",
        props={"electronics_model": "25"},
    )
    ad = IphoneAd.from_olx_json(raw)
    assert ad.modelo == "IPHONE 13 PRO MAX"


def test_iphone_modelo_ausente_extrai_do_titulo():
    raw = _raw_iphone(subject="iPhone SE 2022 novo lacrado", properties=[])
    ad = IphoneAd.from_olx_json(raw)
    assert ad.modelo == "IPHONE SE 2022"


def test_iphone_condicao_com_defeito_usa_mesmo_vocabulario_de_monitor():
    ad = IphoneAd.from_olx_json(_raw_iphone(props={"electronics_condition": "Com defeito ou avarias"}))
    assert ad.condicao == "Com defeito ou avarias"


# --- ComputadorAd -- estrutura real da OLX (categoria "Computadores e
# Desktops"), inspecionada ao vivo antes de escrever o parser.

def _raw_computador(**overrides):
    props = {
        "info_computer_brand": "Dell",
        "info_computer_condition": "Usado - Excelente",
        "info_computer_cpu_brand": "Intel",
        "info_computer_cpu_model": "Intel Core i5",
        "info_computer_ram_size": "8 GB",
        "info_computer_storage_size": "256 GB",
        "info_computer_type": "Computador Completo",
    }
    props.update(overrides.pop("props", {}))
    raw = {
        "listId": 1528989352,
        "subject": "Computador completo Dell i5",
        "priceValue": "R$ 1.399",
        "oldPrice": None,
        "url": "https://es.olx.com.br/x-1528989352",
        "date": 1787313137,
        "locationDetails": {"municipality": "Vitoria", "neighbourhood": "Centro", "uf": "ES"},
        "olxPay": {"transactionalSellerName": "loja", "transactionalSellerRating": None},
        "properties": [{"name": k, "value": v} for k, v in props.items()],
    }
    raw.update(overrides)
    return raw


def test_computador_from_olx_json_le_specs_reais():
    ad = ComputadorAd.from_olx_json(_raw_computador())
    assert ad.categoria == "computador"
    assert ad.marca == "Dell"
    assert ad.condicao == "Usado - Excelente"
    assert ad.cpu_marca == "Intel"
    assert ad.cpu_modelo == "Intel Core i5"
    assert ad.ram_gb == 8
    assert ad.armazenamento_gb == 256
    assert ad.preco == 1399.0


def test_computador_grupo_combina_cpu_e_ram():
    ad = ComputadorAd.from_olx_json(_raw_computador())
    assert ad.grupo == "Intel Core i5 · 8GB RAM"


def test_computador_inclui_monitor_pela_caracteristica_estruturada():
    raw = _raw_computador(props={"info_computer_features": "Inclui acessórios, Inclui monitor, Inclui SSD"})
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.inclui_monitor is True


def test_computador_inclui_monitor_pelo_titulo_quando_falta_a_caracteristica():
    raw = _raw_computador(subject="PC Completo i5 + SSD + Monitor 19\" | Rápido")
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.inclui_monitor is True


def test_computador_nao_inclui_monitor_quando_nao_mencionado():
    ad = ComputadorAd.from_olx_json(_raw_computador())
    assert ad.inclui_monitor is False


def test_computador_condicao_com_defeito_usa_mesmo_vocabulario():
    ad = ComputadorAd.from_olx_json(_raw_computador(props={"info_computer_condition": "Com defeito ou avarias"}))
    assert ad.condicao == "Com defeito ou avarias"


def test_computador_cpu_recuperado_do_titulo_quando_ausente():
    """Achado nos dados reais: 19/250 anúncios vinham sem
    info_computer_cpu_model -- todos caindo no mesmo grupo genérico
    "CPU (?)", misturando um PC de R$450 com um de R$2000+ na mesma
    mediana. A maioria cita o processador no título."""
    raw = _raw_computador(
        subject="PC Gamer Completo i5 + Monitor + Kit Gamer",
        props={"info_computer_cpu_model": None},
    )
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.cpu_modelo == "Intel Core i5"


def test_computador_cpu_recuperado_mesmo_sem_espaco_no_titulo():
    """Caso real: 'Intel Corei3 10100F' -- sem espaço entre 'Core' e 'i3',
    então \\bi3\\b sozinho não bateria."""
    raw = _raw_computador(
        subject='PC Gamer Completo Intel Corei3 10100F, 16Gb DDR4, GT 1030 2Gb, SSD 480Gb, Monitor 21,5"',
        props={"info_computer_cpu_model": None},
    )
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.cpu_modelo == "Intel Core i3"


def test_computador_cpu_ryzen_recuperado_do_titulo():
    raw = _raw_computador(
        subject="PC Gamer Completo Ryzen 5 5500 + Monitor 27 Pol. Curvo",
        props={"info_computer_cpu_model": None},
    )
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.cpu_modelo == "AMD Ryzen 5"


def test_computador_sem_cpu_em_lugar_nenhum_permanece_none():
    """Título genérico de verdade (achado nos dados reais) -- sem CPU
    estruturado nem citado, não tem como recuperar. Fica None, cai no
    grupo "CPU (?)" mesmo -- correto não inventar marca aqui."""
    raw = _raw_computador(subject="Computador completo", props={"info_computer_cpu_model": None})
    ad = ComputadorAd.from_olx_json(raw)
    assert ad.cpu_modelo is None

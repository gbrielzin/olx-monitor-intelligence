from datetime import datetime, timezone

from common.config import settings
from common.schema import MonitorAd


def _reload_storage(tmp_path, nome: str):
    settings.db_path = str(tmp_path / nome)
    from common import storage
    import importlib

    importlib.reload(storage)
    return storage


def _ad(
    listing_id: int,
    preco: float,
    marca: str = "AOC",
    tipo: str = "Monitor Gamer",
    condicao: str | None = None,
    titulo: str | None = None,
    coletado_em=None,
):
    return MonitorAd.model_validate(
        {
            "listing_id": listing_id,
            "titulo": titulo or f"anuncio {listing_id}",
            "url": f"https://x/{listing_id}",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": preco,
            "marca": marca,
            "tipo_monitor": tipo,
            "condicao": condicao,
            "coletado_em": coletado_em or datetime.now(timezone.utc),
        }
    )


def _grupo(marca: str = "AOC", tipo: str = "Monitor Gamer") -> str:
    """Mesmo formato de MonitorAd.grupo -- evita reimplementar a regra
    duas vezes e o teste quebrar sozinho se o formato mudar."""
    return f"{marca} · {tipo}"


def test_preco_mediano_grupo_none_com_amostra_insuficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t1.db")
    storage.init_db()
    storage.upsert_ads([_ad(1, 100.0), _ad(2, 200.0)])  # só 2, mínimo configurado é 5

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("monitor", _grupo()) is None


def test_preco_mediano_grupo_calcula_com_amostra_suficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t2.db")
    storage.init_db()
    precos = [100.0, 200.0, 300.0, 400.0, 500.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("monitor", _grupo()) == 300.0


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
    assert preco_mediano_grupo("monitor", _grupo()) == 300.0  # não 350 (incluiria o 600 inativo)


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
    assert medianas[("monitor", _grupo("AOC", "Monitor Gamer"))] == 300.0
    assert medianas[("monitor", _grupo("LG", "Monitor"))] == 800.0
    assert ("monitor", _grupo("Dell", "Monitor")) not in medianas


def test_medianas_todos_grupos_filtra_por_categoria(tmp_path):
    storage = _reload_storage(tmp_path, "t4b.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([100, 200, 300, 400, 500], start=1)])

    from common.stats import medianas_todos_grupos
    assert medianas_todos_grupos(categoria="monitor")
    assert medianas_todos_grupos(categoria="iphone") == {}


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


def test_avaliar_preco_acima_do_orcamento_maximo_nao_e_oportunidade():
    """'não faz sentido mandar notificação de computador e celular muito
    caro' -- margem % boa não basta, o preço em si tem que caber no
    orçamento real da categoria."""
    from common.stats import avaliar_preco

    av = avaliar_preco(preco=1500.0, mediana=5000.0, categoria="computador")
    assert av.eh_oportunidade is False


def test_avaliar_preco_dentro_do_orcamento_e_oportunidade_normal():
    from common.stats import avaliar_preco

    av = avaliar_preco(preco=900.0, mediana=5000.0, categoria="computador")
    assert av.eh_oportunidade is True


def test_avaliar_preco_sem_categoria_nao_aplica_orcamento():
    """Chamador que não informa categoria (ex: uso genérico/teste isolado)
    não fica travado por um teto que não tem como escolher."""
    from common.stats import avaliar_preco

    av = avaliar_preco(preco=1_000_000.0, mediana=2_000_000.0, categoria=None)
    assert av.eh_oportunidade is True


def test_avaliar_aplica_orcamento_maximo_ponta_a_ponta(tmp_path):
    """Mesmo teste acima, mas pelo caminho real do scraper (avaliar(),
    não avaliar_preco() direto) -- é o que decide se sai notificação."""
    from common.schema import ComputadorAd

    storage = _reload_storage(tmp_path, "t8.db")
    storage.init_db()
    precos = [4000.0, 4200.0, 4400.0, 4600.0, 4800.0]
    ads = [
        ComputadorAd.model_validate(
            {
                "listing_id": i,
                "titulo": f"pc {i}",
                "url": f"https://x/{i}",
                "data_publicacao": "2026-01-01T00:00:00",
                "preco": p,
                "cpu_modelo": "AMD Ryzen 5",
                "ram_gb": 16,
            }
        )
        for i, p in enumerate(precos, start=1)
    ]
    storage.upsert_ads(ads)

    from common.stats import avaliar
    # bem abaixo da mediana (~4400) mas acima do orçamento de computador (R$1000)
    av = avaliar(1500.0, "computador", "AMD Ryzen 5 · 16GB RAM")
    assert av is not None
    assert av.eh_oportunidade is False


def test_avaliar_none_sem_preco(tmp_path):
    storage = _reload_storage(tmp_path, "t5.db")
    storage.init_db()

    from common.stats import avaliar
    assert avaliar(None, "monitor", _grupo()) is None


def test_avaliar_none_com_amostra_insuficiente(tmp_path):
    storage = _reload_storage(tmp_path, "t6.db")
    storage.init_db()

    from common.stats import avaliar
    assert avaliar(500.0, "monitor", _grupo("MarcaQueNaoExiste", "Monitor")) is None


def test_avaliar_retorna_avaliacao_completa(tmp_path):
    storage = _reload_storage(tmp_path, "t7.db")
    storage.init_db()
    precos = [100.0, 200.0, 300.0, 400.0, 500.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import avaliar
    av = avaliar(200.0, "monitor", _grupo())
    assert av is not None
    assert av.mediana == 300.0
    assert av.preco == 200.0


def test_margem_e_confiavel():
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel(None) is True
    assert margem_e_confiavel("Usado - Excelente") is True
    assert margem_e_confiavel("Com defeito ou avarias") is False


def test_margem_e_confiavel_pelo_titulo_quando_condicao_esta_errada():
    """Achado testando o dashboard ao vivo: vendedor descreve o defeito no
    título mas marca a condição estruturada como boa -- o campo sozinho
    não bastava."""
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel("Usado - Excelente", "Monitor AOC COM DEFEITO NÃO LIGA") is False
    assert margem_e_confiavel("Usado - Bom", "Tela trincada, serve pra peças") is False
    assert margem_e_confiavel("Usado - Excelente", "Monitor Samsung 24 polegadas") is True


def test_margem_e_confiavel_cobre_termos_de_iphone():
    """Achado ao vivo (2ª vez): um iPhone com defeito passou pelo primeiro
    regex e chegou no Telegram -- o vocabulário de defeito de celular é
    mais amplo que o de monitor (bateria, iCloud) e não estava coberto."""
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel("Usado - Bom", "iPhone 11 com problema na tela") is False
    assert margem_e_confiavel("Usado - Bom", "iPhone 12 não carrega mais") is False
    assert margem_e_confiavel("Usado - Bom", "iPhone 13 bateria viciada, troca") is False
    assert margem_e_confiavel("Usado - Excelente", "iPhone 11 iCloud bloqueado") is False
    assert margem_e_confiavel("Usado - Bom", "iPhone XR preso no iCloud, vendo assim mesmo") is False
    assert margem_e_confiavel("Usado - Bom", "iPhone 14 rachado no canto") is False
    # "desbloqueado" é sinal bom (aparelho livre), não pode casar com o
    # regex de "bloqueado" por conter a palavra como substring.
    assert margem_e_confiavel("Usado - Excelente", "iPhone 13 128gb desbloqueado de fábrica") is True
    assert margem_e_confiavel("Usado - Excelente", "iPhone 12 sem iCloud, zerado") is True


def test_preco_mediano_grupo_ignora_anuncio_com_defeito(tmp_path):
    """Um anúncio 'com defeito ou avarias' é mercado de sucata, não de
    unidade funcionando -- não pode puxar a mediana pra baixo e fazer
    parecer que qualquer anúncio bom é uma pechincha absurda."""
    storage = _reload_storage(tmp_path, "t8.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    ads.append(_ad(6, 20.0, condicao="Com defeito ou avarias"))  # sucata, preço de peça
    storage.upsert_ads(ads)

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("monitor", _grupo()) == 500.0  # não conta o 20.0


def test_medianas_todos_grupos_ignora_anuncio_com_defeito(tmp_path):
    storage = _reload_storage(tmp_path, "t9.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    ads.append(_ad(6, 20.0, condicao="Com defeito ou avarias"))
    storage.upsert_ads(ads)

    from common.stats import medianas_todos_grupos
    assert medianas_todos_grupos()[("monitor", _grupo())] == 500.0


def test_avaliar_none_para_anuncio_com_defeito_mesmo_barato(tmp_path):
    """Sem esse guard, um anúncio de R$20 'com defeito' contra uma mediana
    de unidade boa (ex. R$500) vira 'oportunidade' com margem de centenas
    de % -- exatamente o bug encontrado ao testar o dashboard no navegador."""
    storage = _reload_storage(tmp_path, "t10.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    storage.upsert_ads(ads)

    from common.stats import avaliar
    assert avaliar(20.0, "monitor", _grupo(), condicao="Com defeito ou avarias") is None
    # o mesmo preço, sem ser "com defeito", seria avaliado normalmente
    av_normal = avaliar(20.0, "monitor", _grupo(), condicao="Usado - Bom")
    assert av_normal is not None
    assert av_normal.eh_oportunidade is True


def test_avaliar_none_quando_titulo_denuncia_defeito_apesar_da_condicao_boa(tmp_path):
    storage = _reload_storage(tmp_path, "t11.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    storage.upsert_ads(ads)

    from common.stats import avaliar
    resultado = avaliar(
        20.0, "monitor", _grupo(),
        condicao="Usado - Excelente", titulo="Monitor AOC 27p COM DEFEITO NÃO LIGA",
    )
    assert resultado is None

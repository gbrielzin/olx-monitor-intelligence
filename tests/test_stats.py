from datetime import datetime, timedelta, timezone

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
    precos = [300.0, 400.0, 500.0, 600.0, 700.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("monitor", _grupo()) == 500.0


def test_preco_mediano_grupo_ignora_anuncio_inativo(tmp_path):
    storage = _reload_storage(tmp_path, "t3.db")
    storage.init_db()
    coleta_1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    coleta_2 = datetime(2026, 1, 1, 12, 12, tzinfo=timezone.utc)
    precos = [300.0, 400.0, 500.0, 600.0, 700.0, 800.0]
    storage.upsert_ads([_ad(i, p, coletado_em=coleta_1) for i, p in enumerate(precos, start=1)])
    # rodada seguinte sem o listing 6 (preço 800) -- fica inativo
    storage.upsert_ads([_ad(i, p, coletado_em=coleta_2) for i, p in enumerate(precos[:5], start=1)])

    from common.stats import preco_mediano_grupo
    assert preco_mediano_grupo("monitor", _grupo()) == 500.0  # não 550 (incluiria o 800 inativo)


def test_medianas_todos_grupos_calcula_todos_de_uma_vez(tmp_path):
    storage = _reload_storage(tmp_path, "t4.db")
    storage.init_db()
    ads = (
        [_ad(i, p, marca="AOC", tipo="Monitor Gamer") for i, p in enumerate([300, 400, 500, 600, 700], start=1)]
        + [_ad(i, p, marca="LG", tipo="Monitor") for i, p in enumerate([600, 700, 800, 900, 1000], start=101)]
        + [_ad(201, 50.0, marca="Dell", tipo="Monitor")]  # só 1 -- abaixo da amostra mínima
    )
    storage.upsert_ads(ads)

    from common.stats import medianas_todos_grupos
    medianas = medianas_todos_grupos()
    assert medianas[("monitor", _grupo("AOC", "Monitor Gamer"))] == 500.0
    assert medianas[("monitor", _grupo("LG", "Monitor"))] == 800.0
    assert ("monitor", _grupo("Dell", "Monitor")) not in medianas


def test_medianas_todos_grupos_filtra_por_categoria(tmp_path):
    storage = _reload_storage(tmp_path, "t4b.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300, 400, 500, 600, 700], start=1)])

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
    precos = [300.0, 400.0, 500.0, 600.0, 700.0]
    storage.upsert_ads([_ad(i, p) for i, p in enumerate(precos, start=1)])

    from common.stats import avaliar
    av = avaliar(400.0, "monitor", _grupo())
    assert av is not None
    assert av.mediana == 500.0
    assert av.preco == 400.0


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
    """Sem esse guard, um anúncio 'com defeito' contra uma mediana de
    unidade boa (R$500) vira 'oportunidade' -- exatamente o bug encontrado
    ao testar o dashboard no navegador. R$350 (não algo tipo R$20) pra
    isolar só a variável 'condição': o mesmo preço já está acima do piso
    de orçamento mínimo de monitor (R$300, ver test_avaliar_preco_abaixo_
    do_piso_nao_e_oportunidade), então só a condição explica a diferença
    de resultado abaixo."""
    storage = _reload_storage(tmp_path, "t10.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    storage.upsert_ads(ads)

    from common.stats import avaliar
    assert avaliar(350.0, "monitor", _grupo(), condicao="Com defeito ou avarias") is None
    # o mesmo preço, sem ser "com defeito", seria avaliado normalmente
    av_normal = avaliar(350.0, "monitor", _grupo(), condicao="Usado - Bom")
    assert av_normal is not None
    assert av_normal.eh_oportunidade is True


def test_avaliar_preco_abaixo_do_piso_nao_e_oportunidade():
    """'iPhone 11 64GB' por R$10 (visto ao vivo, 2026-08-27, condição
    'Usado - Bom' no anúncio real) não bate em nenhuma regra de texto --
    só o preço em si denuncia que não é dado de mercado confiável."""
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB", preco=10.0, categoria="iphone") is False


def test_margem_e_confiavel_preco_no_piso_ou_acima_e_confiavel():
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB", preco=600.0, categoria="iphone") is True
    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB", preco=599.99, categoria="iphone") is False


def test_margem_e_confiavel_sem_preco_ou_categoria_nao_aplica_piso():
    """Compatibilidade com todo call site antigo (2 argumentos só) --
    preco/categoria são opcionais, sem eles o piso simplesmente não roda."""
    from common.stats import margem_e_confiavel

    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB") is True
    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB", preco=10.0) is True  # sem categoria, sem piso
    assert margem_e_confiavel("Usado - Bom", "iPhone 11 64 GB", preco=10.0, categoria=None) is True


def test_medianas_todos_grupos_ignora_anuncio_abaixo_do_piso(tmp_path):
    """Mesmo espírito de test_medianas_todos_grupos_ignora_anuncio_com_
    defeito, mas pro piso de preço: um anúncio implausivelmente barato não
    pode puxar a mediana do grupo pra baixo mesmo sem estar marcado como
    defeituoso."""
    storage = _reload_storage(tmp_path, "t13.db")
    storage.init_db()
    ads = [_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)]
    ads.append(_ad(6, 50.0, condicao="Usado - Bom"))  # abaixo do piso de monitor (300), sem ser "defeito"
    storage.upsert_ads(ads)

    from common.stats import medianas_todos_grupos
    assert medianas_todos_grupos()[("monitor", _grupo())] == 500.0  # não conta o 50.0


def test_avaliar_aplica_piso_minimo_ponta_a_ponta(tmp_path):
    """Mesmo teste acima, mas pelo caminho real do scraper (avaliar(), não
    margem_e_confiavel() direto) -- é o que decide se sai notificação."""
    from common.schema import IphoneAd

    storage = _reload_storage(tmp_path, "t14.db")
    storage.init_db()
    precos = [900.0, 950.0, 1000.0, 1050.0, 1100.0]
    ads = [
        IphoneAd.model_validate(
            {
                "listing_id": i,
                "titulo": f"iphone 11 {i}",
                "url": f"https://x/{i}",
                "data_publicacao": "2026-01-01T00:00:00",
                "preco": p,
                "modelo": "IPHONE 11",
                "armazenamento_gb": 64,
            }
        )
        for i, p in enumerate(precos, start=1)
    ]
    storage.upsert_ads(ads)

    from common.stats import avaliar
    # "iPhone 11 64GB" por R$10 de verdade (achado ao vivo) não pode virar notificação
    assert avaliar(10.0, "iphone", "IPHONE 11 · 64GB", condicao="Usado - Bom") is None
    # preço real dentro da faixa plausível continua avaliado normalmente
    av = avaliar(700.0, "iphone", "IPHONE 11 · 64GB", condicao="Usado - Bom")
    assert av is not None
    assert av.eh_oportunidade is True


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


# --- grava_snapshot_diario / tendencia_grupo (histórico de mercado) ---

def _insere_mediana_diaria(storage, data: str, grupo: str, mediana: float, categoria: str = "monitor", amostra: int = 5) -> None:
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO medianas_diarias (data, categoria, grupo, mediana, amostra, registrado_em) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (data, categoria, grupo, mediana, amostra, datetime.now(timezone.utc).isoformat()),
        )


def test_grava_snapshot_diario_grava_uma_linha_por_grupo_confiavel(tmp_path):
    storage = _reload_storage(tmp_path, "snap1.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    from common.stats import grava_snapshot_diario
    assert grava_snapshot_diario("monitor") == 1

    hoje = datetime.now(timezone.utc).date().isoformat()
    with storage.get_connection() as conn:
        linha = conn.execute(
            "SELECT mediana, amostra FROM medianas_diarias WHERE data=? AND categoria='monitor' AND grupo=?",
            (hoje, _grupo()),
        ).fetchone()
    assert linha == (500.0, 5)


def test_grava_snapshot_diario_idempotente_no_mesmo_dia(tmp_path):
    storage = _reload_storage(tmp_path, "snap2.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    from common.stats import grava_snapshot_diario
    assert grava_snapshot_diario("monitor") == 1
    assert grava_snapshot_diario("monitor") == 0  # já gravou hoje -- não duplica

    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM medianas_diarias").fetchone()[0]
    assert total == 1


def test_grava_snapshot_diario_ignora_grupo_sem_amostra_minima(tmp_path):
    storage = _reload_storage(tmp_path, "snap3.db")
    storage.init_db()
    storage.upsert_ads([_ad(1, 300.0), _ad(2, 400.0)])  # só 2, mínimo configurado é 5

    from common.stats import grava_snapshot_diario
    assert grava_snapshot_diario("monitor") == 0
    with storage.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM medianas_diarias").fetchone()[0]
    assert total == 0


def test_grava_snapshot_diario_filtra_por_categoria(tmp_path):
    storage = _reload_storage(tmp_path, "snap4.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    from common.stats import grava_snapshot_diario
    assert grava_snapshot_diario("iphone") == 0  # não tem nenhum anúncio dessa categoria
    assert grava_snapshot_diario("monitor") == 1


def test_tendencia_grupo_none_sem_mediana_atual_confiavel(tmp_path):
    storage = _reload_storage(tmp_path, "tend1.db")
    storage.init_db()

    from common.stats import tendencia_grupo
    assert tendencia_grupo("monitor", _grupo()) is None


def test_tendencia_grupo_sem_historico_retorna_indeterminado(tmp_path):
    storage = _reload_storage(tmp_path, "tend2.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    from common.stats import tendencia_grupo
    t = tendencia_grupo("monitor", _grupo(), dias=30)
    assert t is not None
    assert t.mediana_atual == 500.0
    assert t.mediana_periodo is None
    assert t.dias_disponiveis == 0
    assert t.direcao == "indeterminado"


def test_tendencia_grupo_dias_insuficientes_nao_fabrica_mediana_periodo(tmp_path):
    """dias_disponiveis (3) < dias pedido (30) -- mediana_periodo tem que
    ficar None, não uma média calculada só sobre os 3 dias que existem."""
    storage = _reload_storage(tmp_path, "tend3.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    hoje = datetime.now(timezone.utc).date()
    for delta, mediana in [(2, 400.0), (1, 450.0), (0, 500.0)]:
        _insere_mediana_diaria(storage, (hoje - timedelta(days=delta)).isoformat(), _grupo(), mediana)

    from common.stats import tendencia_grupo
    t = tendencia_grupo("monitor", _grupo(), dias=30)
    assert t.dias_disponiveis == 3
    assert t.mediana_periodo is None
    assert t.direcao == "subindo"  # 400 -> 500 = +25%, calculado com o que existe


def test_tendencia_grupo_mediana_periodo_quando_dias_disponiveis_e_suficiente(tmp_path):
    storage = _reload_storage(tmp_path, "tend4.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    hoje = datetime.now(timezone.utc).date()
    for delta, mediana in [(1, 500.0), (0, 510.0)]:
        _insere_mediana_diaria(storage, (hoje - timedelta(days=delta)).isoformat(), _grupo(), mediana)

    from common.stats import tendencia_grupo
    t = tendencia_grupo("monitor", _grupo(), dias=2)
    assert t.dias_disponiveis == 2
    assert t.mediana_periodo == 505.0
    assert t.direcao == "estavel"  # (510-500)/500 = 2%, dentro do limiar de 5%


def test_tendencia_grupo_direcao_caindo(tmp_path):
    storage = _reload_storage(tmp_path, "tend5.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    hoje = datetime.now(timezone.utc).date()
    for delta, mediana in [(1, 500.0), (0, 400.0)]:
        _insere_mediana_diaria(storage, (hoje - timedelta(days=delta)).isoformat(), _grupo(), mediana)

    from common.stats import tendencia_grupo
    t = tendencia_grupo("monitor", _grupo(), dias=2)
    assert t.direcao == "caindo"


def test_tendencia_grupo_um_dia_disponivel_e_indeterminado(tmp_path):
    """1 ponto só não define direção nenhuma -- sem isso, primeira==ultima
    (o mesmo ponto) sempre daria variação 0% e pareceria 'estável' por
    acidente, não porque o preço realmente ficou estável."""
    storage = _reload_storage(tmp_path, "tend6.db")
    storage.init_db()
    storage.upsert_ads([_ad(i, p) for i, p in enumerate([300.0, 400.0, 500.0, 600.0, 700.0], start=1)])

    hoje = datetime.now(timezone.utc).date().isoformat()
    _insere_mediana_diaria(storage, hoje, _grupo(), 450.0)

    from common.stats import tendencia_grupo
    t = tendencia_grupo("monitor", _grupo(), dias=30)
    assert t.dias_disponiveis == 1
    assert t.direcao == "indeterminado"
    assert t.amostra_periodo == 5

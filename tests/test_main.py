from common.config import Regiao


def test_rodar_coleta_iphone_todas_regioes_chama_uma_vez_por_regiao(monkeypatch):
    """Trava a mecânica do loop novo: 1 chamada de rodar_coleta por região,
    sequencial (com sleep entre elas), cada uma com a UF/chat_id/URL certos
    -- orquestração nova, fácil de errar por cópia/colar."""
    from scraper import main

    chamadas = []
    monkeypatch.setattr(main, "rodar_coleta", lambda **kw: chamadas.append(kw))

    sleeps = []
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))

    monkeypatch.setattr(
        main.settings,
        "iphone_regioes",
        [
            Regiao(uf="ES", nome="Espírito Santo", chat_id="1", publico=False),
            Regiao(uf="SP", nome="São Paulo", chat_id="2", publico=True),
        ],
    )

    main.rodar_coleta_iphone_todas_regioes()

    assert [c["uf"] for c in chamadas] == ["ES", "SP"]
    assert [c["categoria"] for c in chamadas] == ["iphone", "iphone"]
    assert chamadas[0]["chat_id_oportunidade"] == "1"
    assert chamadas[1]["chat_id_oportunidade"] == "2"
    assert chamadas[0]["search_url"] == "https://www.olx.com.br/estado-es?q=iphone"
    assert chamadas[1]["search_url"] == "https://www.olx.com.br/estado-sp?q=iphone"
    # sleep só ENTRE regiões, nunca antes da primeira
    assert sleeps == [main.settings.intervalo_entre_regioes_segundos]


def test_rodar_coleta_iphone_todas_regioes_com_1_regiao_nao_da_sleep(monkeypatch):
    """Comportamento de hoje (só ES): 1 chamada, nenhum delay -- não pode
    ficar mais lento pra quem não configurou região nenhuma nova."""
    from scraper import main

    chamadas = []
    monkeypatch.setattr(main, "rodar_coleta", lambda **kw: chamadas.append(kw))
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        main.settings, "iphone_regioes",
        [Regiao(uf="ES", nome="Espírito Santo", chat_id="1", publico=False)],
    )

    main.rodar_coleta_iphone_todas_regioes()

    assert len(chamadas) == 1
    assert sleeps == []


def test_rodar_coleta_monitor_e_computador_ainda_funcionam_mas_com_uf_es(monkeypatch):
    """monitor/computador não são mais agendadas (main()), mas as funções
    continuam definidas e chamáveis -- reativação futura só precisa voltar
    a agendá-las."""
    from scraper import main

    chamadas = []
    monkeypatch.setattr(main, "rodar_coleta", lambda **kw: chamadas.append(kw))

    main.rodar_coleta_monitor()
    main.rodar_coleta_computador()

    assert chamadas[0]["categoria"] == "monitor"
    assert chamadas[0]["uf"] == "ES"
    assert chamadas[1]["categoria"] == "computador"
    assert chamadas[1]["uf"] == "ES"

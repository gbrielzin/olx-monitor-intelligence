from common.config import Regiao, Settings


def test_default_iphone_regiao_usa_telegram_chat_id_pessoal():
    """Sem IPHONE_REGIOES no .env, o comportamento tem que ficar idêntico
    ao de antes dessa configuração existir: só a região pessoal (ES), no
    mesmo chat pessoal já configurado -- ninguém precisa mexer no .env
    pra continuar funcionando."""
    s = Settings(telegram_chat_id="123456", _env_file=None)
    assert s.iphone_regioes == [
        Regiao(uf="ES", nome="Espírito Santo", chat_id="123456", publico=False)
    ]


def test_iphone_regioes_decodifica_lista_json_da_env_var(monkeypatch):
    """IPHONE_REGIOES é uma lista JSON crua no .env -- pydantic-settings
    decodifica isso automaticamente num campo list[Regiao] tipado."""
    monkeypatch.setenv(
        "IPHONE_REGIOES",
        '[{"uf":"ES","nome":"Espírito Santo","chat_id":"1","publico":false},'
        '{"uf":"SP","nome":"São Paulo","chat_id":"2","publico":true}]',
    )
    s = Settings(_env_file=None)
    assert len(s.iphone_regioes) == 2
    assert s.iphone_regioes[0] == Regiao(uf="ES", nome="Espírito Santo", chat_id="1", publico=False)
    assert s.iphone_regioes[1] == Regiao(uf="SP", nome="São Paulo", chat_id="2", publico=True)


def test_iphone_regioes_setado_no_env_nao_aciona_o_default():
    """Uma única região customizada no .env não deve ser sobrescrita pelo
    fallback -- o model_validator só entra em ação com a lista vazia."""
    s = Settings(
        telegram_chat_id="123456",
        iphone_regioes=[Regiao(uf="SP", nome="São Paulo", chat_id="999", publico=True)],
        _env_file=None,
    )
    assert len(s.iphone_regioes) == 1
    assert s.iphone_regioes[0].uf == "SP"


def test_iphone_search_url_template_usa_uf_minusculo():
    s = Settings(_env_file=None)
    assert s.iphone_search_url_template.format(uf="sp") == "https://www.olx.com.br/estado-sp?q=iphone"

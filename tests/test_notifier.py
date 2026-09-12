from common.config import settings


class _RespostaOk:
    def raise_for_status(self):
        pass


def _capturar_chamadas(monkeypatch):
    """Substitui requests.post por um fake que só grava o payload enviado
    -- projeto não usa lib de mock, monkeypatch nativo do pytest basta pra
    isso (ver scraper/notifier.py: só uma chamada POST, sem estado)."""
    chamadas = []

    def _post_fake(url, json, timeout):
        chamadas.append(json)
        return _RespostaOk()

    monkeypatch.setattr("notifier.requests.post", _post_fake)
    return chamadas


def test_chat_id_explicito_tem_prioridade_sobre_o_padrao(monkeypatch):
    from notifier import enviar_telegram

    settings.telegram_bot_token = "token-fake"
    settings.telegram_chat_id = "pessoal-123"
    chamadas = _capturar_chamadas(monkeypatch)

    enviar_telegram("oportunidade em SP!", chat_id="grupo-sp-456")

    assert chamadas == [{"chat_id": "grupo-sp-456", "text": "oportunidade em SP!"}]


def test_sem_chat_id_explicito_usa_o_padrao(monkeypatch):
    """Alerta de erro/sanidade (scraper/main.py chama enviar_telegram sem
    chat_id) tem que cair sempre no chat pessoal do operador, nunca num
    grupo público de região."""
    from notifier import enviar_telegram

    settings.telegram_bot_token = "token-fake"
    settings.telegram_chat_id = "pessoal-123"
    chamadas = _capturar_chamadas(monkeypatch)

    enviar_telegram("scraper (iPhone SP) possivelmente quebrado")

    assert chamadas == [{"chat_id": "pessoal-123", "text": "scraper (iPhone SP) possivelmente quebrado"}]


def test_chat_id_vazio_cai_pro_padrao(monkeypatch):
    """Região recém-cadastrada em IPHONE_REGIOES antes do grupo do Telegram
    existir de verdade (chat_id="") não pode fazer a notificação sumir --
    cai pro chat pessoal em vez de ficar sem destino."""
    from notifier import enviar_telegram

    settings.telegram_bot_token = "token-fake"
    settings.telegram_chat_id = "pessoal-123"
    chamadas = _capturar_chamadas(monkeypatch)

    enviar_telegram("oportunidade", chat_id="")

    assert chamadas == [{"chat_id": "pessoal-123", "text": "oportunidade"}]


def test_sem_credenciais_pula_notificacao(monkeypatch):
    settings.telegram_bot_token = ""
    settings.telegram_chat_id = ""
    from notifier import enviar_telegram

    chamado = False

    def _post_fake(*args, **kwargs):
        nonlocal chamado
        chamado = True

    monkeypatch.setattr("notifier.requests.post", _post_fake)

    enviar_telegram("mensagem qualquer")  # não deve levantar nem chamar a API

    assert chamado is False

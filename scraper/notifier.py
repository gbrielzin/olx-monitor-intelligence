"""Notificação via Telegram Bot API.

Usa requests puro contra o endpoint sendMessage em vez da lib
python-telegram-bot: só precisamos ENVIAR mensagem, nunca receber ou
lidar com updates — trazer um framework de bot inteiro pra isso seria
peso morto na imagem Docker. Uma chamada POST resolve.

Se as credenciais não estiverem no .env, a notificação é pulada com um
log de aviso em vez de derrubar o scraper — ver common/config.py.

Sem parse_mode (texto puro, sem Markdown): a mensagem embute texto que
a gente não controla -- título de anúncio (vendedor escreve o que
quiser) e mensagem de exceção -- e um `_`/`*` desbalanceado nesse texto
fazia o Telegram rejeitar com 400 "can't parse entities", derrubando em
silêncio justo o alerta que devia ser a rede de segurança. Visto ao
vivo: um erro de banco com "historico_precos.preco" no texto (os `_`
do nome da coluna) bastou pra isso acontecer.
"""

import logging

import requests

from common.config import settings

logger = logging.getLogger(__name__)


def enviar_telegram(mensagem: str, chat_id: str | None = None) -> None:
    """`chat_id` opcional pra rotear pro grupo de uma região específica
    (oportunidade de iPhone de um estado com grupo próprio) — sem ele, cai
    no chat pessoal do operador (`settings.telegram_chat_id`), que é onde
    TODO alerta de erro/sanidade/scraper-quebrado deve ir sempre, nunca
    num grupo público."""
    destino = chat_id or settings.telegram_chat_id
    if not settings.telegram_bot_token or not destino:
        logger.warning("Telegram não configurado no .env — notificação pulada: %s", mensagem[:80])
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": destino,
                "text": mensagem,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        # Falha ao notificar não pode derrubar o scraper.
        logger.error("Falha ao enviar Telegram: %s", e)

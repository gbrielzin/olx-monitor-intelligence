"""Notificação via Telegram Bot API.

Usa requests puro contra o endpoint sendMessage em vez da lib
python-telegram-bot: só precisamos ENVIAR mensagem, nunca receber ou
lidar com updates — trazer um framework de bot inteiro pra isso seria
peso morto na imagem Docker. Uma chamada POST resolve.

Se as credenciais não estiverem no .env, a notificação é pulada com um
log de aviso em vez de derrubar o scraper — ver common/config.py.
"""

import logging

import requests

from common.config import settings

logger = logging.getLogger(__name__)


def enviar_telegram(mensagem: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram não configurado no .env — notificação pulada: %s", mensagem[:80])
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": mensagem,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        # Falha ao notificar não pode derrubar o scraper.
        logger.error("Falha ao enviar Telegram: %s", e)

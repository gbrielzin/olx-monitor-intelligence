from common.config import settings
from common.schema import MonitorAd
from scraper.auditoria_ia import auditar_anuncio


def _ad():
    return MonitorAd.model_validate(
        {
            "listing_id": 1,
            "titulo": "Monitor AOC 24 polegadas",
            "url": "https://x/1",
            "data_publicacao": "2026-01-01T00:00:00",
            "preco": 500.0,
            "marca": "AOC",
            "tipo_monitor": "Monitor Gamer",
        }
    )


def test_desativado_por_padrao_nao_chama_api():
    settings.ia_auditoria_ativa = False
    auditar_anuncio(_ad())  # não deve levantar nem precisar de rede/API key


def test_ativado_sem_api_key_nao_quebra():
    settings.ia_auditoria_ativa = True
    settings.anthropic_api_key = ""
    try:
        auditar_anuncio(_ad())  # sem chave configurada, só loga e retorna
    finally:
        settings.ia_auditoria_ativa = False

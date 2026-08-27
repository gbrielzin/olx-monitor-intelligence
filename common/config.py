"""Configuração central do projeto.

Usa pydantic-settings em vez de os.getenv() espalhado pelo código: os
valores são tipados, validados uma única vez na subida do processo, e
qualquer variável ausente ou malformada quebra cedo (na inicialização),
não no meio de uma coleta às 3h da manhã.

Todos os campos têm default — nenhum é obrigatório. Isso é proposital:
o scraper e o dashboard devem subir mesmo sem .env configurado (ex: em
teste local ou CI), só que com notificação do Telegram desativada até
as credenciais serem preenchidas. Ver notifier.py.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Telegram (opcional — sem isso, só perde a notificação) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Alvo da coleta ---
    # Nome ficou de quando só existia uma categoria -- é a busca de
    # monitor, especificamente (ver iphone_search_url abaixo). Não
    # renomeado pra não quebrar quem já tem OLX_SEARCH_URL customizado
    # no .env.
    olx_search_url: str = "https://www.olx.com.br/informatica/monitores/estado-es?q=monitor"
    iphone_search_url: str = "https://www.olx.com.br/estado-es?q=iphone"
    computador_search_url: str = "https://www.olx.com.br/estado-es?q=computador%20completo"
    scrape_interval_minutes: int = 12
    max_paginas: int = 5  # teto por rodada; o loop já para sozinho se a página vier com <50 itens reais
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    request_timeout_seconds: int = 15

    # --- Checkpoint de sanidade ---
    min_ads_ratio: float = 0.5  # abaixo de 50% da média recente = suspeito
    min_price_field_ratio: float = 0.7  # % mínima de anúncios com preço detectado

    # --- Estatística de oportunidade ---
    oportunidade_limiar: float = 0.75  # preço <= 75% da mediana do grupo = oportunidade
    oportunidade_amostra_minima: int = 5  # não confia na mediana com menos que isso

    # --- Negociação (o preço anunciado da OLX não é o preço final) ---
    # Chute inicial documentado, não dado calibrado — ajuste aqui conforme o
    # desconto real que você conseguir negociando. Usado só pra enriquecer o
    # alerta com uma estimativa de custo/margem; não muda a decisão de
    # "é oportunidade" (essa continua comparando o preço anunciado, que é o
    # único número verificável antes de negociar).
    desconto_negociacao_esperado: float = 0.10

    # --- Orçamento máximo por categoria (teto de preço, não de margem) ---
    # Por mais boa que a margem % pareça, acima disso não vira notificação --
    # fora da faixa que você realmente compraria. Chutes iniciais a partir
    # do que você já confirmou: monitor R$300–600 (seção "Veredito" do
    # relatório), computador R$400–1000 (sua faixa de giro rápido). iPhone
    # é o chute mais largo dos três, por falta de faixa que você tenha
    # confirmado -- ajuste aqui se não bater com o que você compraria.
    orcamento_maximo_monitor: float = 600.0
    orcamento_maximo_computador: float = 1000.0
    orcamento_maximo_iphone: float = 2000.0

    # --- Banco de dados ---
    db_path: str = "/data/olx_monitor.db"


settings = Settings()

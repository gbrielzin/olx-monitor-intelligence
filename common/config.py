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

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Regiao(BaseModel):
    """Um estado onde o scraper de iPhone roda. `publico` é só documentação
    humana no .env (pra lembrar qual grupo é pessoal vs. compartilhado) --
    nenhum código ramifica por ele: a região pessoal padrão (ES) já usa
    `chat_id=telegram_chat_id`, então rotear por `chat_id` direto já dá o
    resultado certo sem checar `publico`."""

    uf: str
    nome: str
    chat_id: str
    publico: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Telegram (opcional — sem isso, só perde a notificação) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Alvo da coleta ---
    # Nome ficou de quando só existia uma categoria -- é a busca de
    # monitor, especificamente. Não renomeado pra não quebrar quem já tem
    # OLX_SEARCH_URL customizado no .env. monitor/computador pararam de
    # ser agendados (mercado se mostrou ineficaz) -- URLs ficam aqui só
    # porque o código ainda existe (scraper/main.py:rodar_coleta_monitor/
    # rodar_coleta_computador), caso sejam reativados algum dia.
    olx_search_url: str = "https://www.olx.com.br/informatica/monitores/estado-es?q=monitor"
    computador_search_url: str = "https://www.olx.com.br/estado-es?q=computador%20completo"

    # --- iPhone: roda em N regiões (estados) na mesma execução, cada uma
    # podendo ter seu próprio grupo do Telegram. `{uf}` no template vira a
    # sigla em minúsculo de cada região (ver Regiao.uf). Sem IPHONE_REGIOES
    # no .env, cai no default abaixo (model_validator) -- só a região
    # pessoal do usuário (ES), no chat pessoal, comportamento idêntico ao
    # de antes desta configuração existir. Pra adicionar um estado novo:
    # IPHONE_REGIOES='[{"uf":"ES","nome":"Espírito Santo","chat_id":"...","publico":false},
    #                   {"uf":"SP","nome":"São Paulo","chat_id":"...","publico":true}]'
    iphone_search_url_template: str = "https://www.olx.com.br/estado-{uf}?q=iphone"
    iphone_regioes: list[Regiao] = Field(default_factory=list)
    # Delay entre a rodada de uma região de iPhone e a próxima, na mesma
    # execução -- mesmo espírito do delay entre páginas (fetcher/main.py):
    # scraper deliberadamente discreto, não maximiza cobertura simultânea.
    intervalo_entre_regioes_segundos: float = 2.0

    scrape_interval_minutes: int = 12
    # 10 = até 500 anúncios/categoria/rodada. Era 5 (250) -- o banco mostrava
    # a MESMA contagem exata (250) em toda rodada nas 3 categorias, sinal de
    # que o teto estava cortando antes do loop achar uma página curta de
    # verdade (a condição de parada natural, logo abaixo). Sem isso, anúncio
    # empurrado pra fora da janela virava "sumiço" falso -- ver storage.py.
    max_paginas: int = 10  # o loop já para sozinho se a página vier com <50 itens reais
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
    orcamento_maximo_iphone: float = 1500.0  # era 2000 (chute) -- 1500 confirmado por voce em 2026-08-27

    # --- Piso de preço por categoria (preço bom demais pra ser real) ---
    # Visto ao vivo em 2026-08-27: "iPhone 11 64GB" por R$10 (11011% de
    # "margem"), "TROCO POR PC COMPLETO" por R$1 (133233%), monitores a
    # R$50-100 marcados "Bom"/"Excelente" -- nenhum bate no filtro de texto
    # (margem_e_confiavel), porque não é sobre defeito no título, é preço
    # implausível pro que é: golpe, erro de digitação, anúncio "a
    # combinar"/troca com preço-placeholder, ou item errado (acessório)
    # caindo na categoria por engano. Abaixo do piso, o anúncio some da
    # mediana do grupo E de virar notificação -- mas continua salvo em
    # `anuncios` normalmente, só não conta como dado de mercado confiável.
    # Valores = mesma faixa já confirmada em orcamento_maximo (monitor:
    # abaixo de R$300 o relatório já apontava mais risco de defeito;
    # computador: piso da sua faixa de giro rápido; iphone: seu pedido).
    orcamento_minimo_monitor: float = 300.0
    orcamento_minimo_computador: float = 400.0
    orcamento_minimo_iphone: float = 600.0

    # --- Auditoria de anúncios novos via IA (opcional, custa por chamada) ---
    # Compara título x campos extraídos de cada anúncio NOVO (nunca dos que só
    # seguem ativos) via LLM, flagra inconsistência -- ver
    # scraper/auditoria_ia.py. Desativado por padrão: precisa de
    # anthropic_api_key preenchida pra ligar.
    ia_auditoria_ativa: bool = False
    anthropic_api_key: str = ""
    # claude-opus-5 é o modelo mais caro da Anthropic -- ajuste aqui pra um
    # mais barato (ex: claude-haiku-4-5) depois de ver o custo real por
    # rodada (só os anúncios novos de cada região, não o catálogo inteiro).
    ia_modelo: str = "claude-opus-5"

    # --- Resumo diário agentic (opcional, custa 1 chamada de IA por dia) ---
    # Junta oportunidades ativas + tendência de preço de iPhone (todas as
    # regiões) num panorama, pede pro LLM escrever um resumo corrido e manda
    # por Telegram de manhã, só pro chat pessoal (não replica por região) --
    # ver scraper/resumo_diario.py. Reaproveita anthropic_api_key/ia_modelo
    # acima. Desativado por padrão.
    resumo_diario_ativo: bool = False
    resumo_diario_hora_utc: int = 11  # ~8h em Vitória-ES (UTC-3)

    # --- Banco de dados ---
    db_path: str = "/data/olx_monitor.db"

    @model_validator(mode="after")
    def _default_iphone_regiao_pessoal(self) -> "Settings":
        """Sem IPHONE_REGIOES no .env, roda só a região pessoal (ES) no
        chat pessoal -- precisa ser um validator (não um default de campo)
        porque depende de `telegram_chat_id`, outro campo desta mesma
        classe, só resolvido depois que o .env é lido."""
        if not self.iphone_regioes:
            self.iphone_regioes = [
                Regiao(uf="ES", nome="Espírito Santo", chat_id=self.telegram_chat_id, publico=False)
            ]
        return self


settings = Settings()

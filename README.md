# OLX Monitor Intelligence

Sistema de arbitragem informacional: monitora anúncios de usados na
OLX, calcula o preço justo de mercado (mediana por grupo comparável) e
avisa por Telegram quando um anúncio aparece — ou cai de preço —
abaixo dele, com um dashboard Streamlit pra acompanhar tendência de
preço por grupo.

**Hoje ativo pra iPhone, em múltiplas UFs** (uma execução cobre vários
estados, cada um com seu próprio grupo de alerta). Monitor gamer e
computador completo foram as categorias originais do MVP: rodaram em
paralelo por semanas, mas o próprio dado coletado mostrou mercado
eficiente demais pra sustentar a tese de arbitragem, e foram
descontinuadas (código ainda existe, só parou de ser agendado — ver
`common/config.py`). A decisão e os números por trás dela estão em
[`docs/CASE_DATA_ANALYTICS.md`](docs/CASE_DATA_ANALYTICS.md).

> Pra profundidade técnica de backend/infra, ver
> [`docs/OLX_DEEP_DIVE.md`](docs/OLX_DEEP_DIVE.md). Documentação completa
> em [`docs/`](docs/).

## Como rodar

```bash
cp .env.example .env
# edite o .env e preencha pelo menos TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
# (o projeto sobe sem isso, só fica sem notificação)

docker-compose up --build
```

Dashboard em `http://localhost:8501`. O scraper roda em background,
sem porta exposta.

## Rodando os testes localmente (sem Docker)

```bash
pip install -r scraper/requirements.txt -r dashboard/requirements.txt pytest
PYTHONPATH=. pytest tests/ -v
```

Use **Python 3.11** (a mesma versão do `python:3.11-slim` dos Dockerfiles) —
é a versão testada. Os pinos do `requirements.txt` (`pydantic`, `pandas`)
não têm wheel pronta pra versões de Python muito mais novas, então
`pip install` tenta compilar do zero e falha sem toolchain de C/Rust
instalada. Isso não afeta produção (que sempre usa 3.11 via Docker), só
quem rodar os testes fora do container numa versão de Python diferente.

## Arquitetura

```
common/      -- config, schema (Pydantic) e storage (SQLite), compartilhados
scraper/     -- coleta: fetch -> parse -> sanidade -> diff -> save -> alerta
dashboard/   -- Streamlit, só leitura do mesmo banco
data/        -- banco SQLite (gitignored, criado em runtime)
```

**Scraper via `requests`, não Playwright.** A OLX renderiza os
anúncios no servidor e embute os dados como JSON dentro do próprio
HTML (payload RSC do Next.js) — dá pra ler tudo com uma requisição
HTTP simples, sem abrir browser. Ver `scraper/parser.py` para o
porquê e como isso é extraído com segurança (balanceamento de
colchete + desescape de string JS, não regex ingênuo).

**SQLite em modo WAL.** Permite o scraper escrever e o dashboard ler
ao mesmo tempo sem `database is locked`. Ver `common/storage.py`.

**Sem tentativa de burlar Cloudflare.** O scraper é deliberadamente
discreto: varredura enxuta, sessão HTTP reaproveitada, sem rotação de
proxy nem CAPTCHA solver. Ver o checkpoint de sanidade abaixo — é a
rede de segurança para quando (não se) a OLX mudar o front-end.

**Checkpoint de sanidade.** Antes de gravar qualquer coisa, o scraper
verifica se a coleta faz sentido (zero anúncios, maioria sem preço,
queda abrupta vs. histórico). Se algo estiver errado, nada é gravado
e um alerta sai pelo Telegram — errar por excesso de cautela é mais
barato do que enfiar lixo no banco por dias sem ninguém notar.

## Segurança / portfólio público

- Todo segredo (token do Telegram) vem do `.env`, lido via
  `pydantic-settings` — nunca hardcoded no código.
- `.gitignore` bloqueia `.env` e a pasta `data/` (banco local) de
  irem pro GitHub. Confira sempre com `git status` antes do primeiro
  commit que nenhum dos dois aparece como "untracked" pronto pra
  subir.
- `.env.example` documenta as variáveis esperadas sem expor valores
  reais — é o que substitui o `.env` no repositório público.

## Limitações conhecidas (documentadas, não escondidas)

- **Alertas de oportunidade têm partida fria.** A camada estatística
  (`common/stats.py`) só confia na mediana de um grupo (marca + tipo)
  com pelo menos 5 anúncios históricos. Nos primeiros dias, não vai
  sair alerta de oportunidade — só depois que o histórico acumular.
- **`requests` pode parar de funcionar.** Se a OLX endurecer a
  proteção a ponto de bloquear requisições simples, o sintoma vai
  aparecer primeiro no checkpoint de sanidade (Telegram avisando
  "possivelmente quebrado"). Nesse ponto, a decisão a tomar é reduzir
  ainda mais a frequência antes de considerar qualquer alternativa.
- **Extração de JSON depende do formato atual do Next.js da OLX.** Se
  a OLX mudar de framework ou de como serializa os dados no HTML, o
  parser quebra (com erro claro, não silencioso) e precisa de
  ajuste manual.

## Roadmap (próximos passos já pensados, não implementados)

- **Mercado Livre como segunda plataforma** — a coluna `plataforma` já
  existe em todo o schema e no banco, propositalmente, pra isso não
  exigir migração quando chegar a hora.
- **Bicicletas / ferramentas elétricas como terceira categoria** —
  reaproveita quase o parser inteiro, só troca o dicionário de specs.
- **Produtização (caminho Híbrido)** — validar a tese pessoalmente por
  alguns meses, documentar os resultados reais, e só então decidir se
  vira um SaaS de alerta pra outros revendedores (modelo
  camelcamelcamel/Keepa/Industry Alerts).

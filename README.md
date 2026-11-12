# OLX Monitor Intelligence

Sistema de arbitragem informacional: monitora anúncios de usados na
OLX, calcula o preço justo de mercado (mediana por grupo comparável) e
avisa por Telegram quando um anúncio aparece — ou cai de preço —
abaixo dele, com um dashboard Streamlit pra acompanhar tendência de
preço por grupo.

**Hoje ativo pra iPhone, só no Espírito Santo.** O código suporta vários
estados na mesma execução (variável `IPHONE_REGIOES`, cada um com seu
próprio grupo de alerta), mas só o ES é coletado hoje. Monitor gamer e
computador completo foram as categorias originais do MVP: rodaram em
paralelo por semanas, mas o próprio dado coletado mostrou que a
"oportunidade" aparente vinha de grupos heterogêneos, não de mercado
mal precificado, e foram descontinuadas (código ainda existe, só parou de ser agendado — ver
`common/config.py`). A decisão e os números por trás dela estão em
[`docs/CASE_DATA_ANALYTICS.md`](docs/CASE_DATA_ANALYTICS.md).

> Pra profundidade técnica de backend/infra, ver
> [`docs/OLX_DEEP_DIVE.md`](docs/OLX_DEEP_DIVE.md). Documentação completa
> em [`docs/`](docs/).

## Em uma página

**Pergunta:** dá pra transformar "achar boa oferta de usado" num problema de
dados — coletar sistematicamente, definir um preço de referência confiável e
avisar quando um anúncio foge muito dele?

**O que roda:** coleta (a cada 12 min) → validação (Pydantic) → banco
(SQLite) → mediana por grupo comparável → alerta no Telegram → dashboard
(Streamlit). Escopo atual: iPhone, Espírito Santo.

**O que os dados mostraram** (aba *Resumo* do dashboard, calculada ao vivo):

- Monitor parecia ter mais "oportunidade" que iPhone (23% vs 5% dos anúncios
  abaixo de 75% da mediana), mas o grupo dele é heterogêneo — dispersão não é
  oportunidade.
- iPhones anunciados a ≤75% da mediana somem do ar em ~1,9 dia, contra ~3,0
  nas demais faixas (descritivo, com ressalvas documentadas).
- A queda real mediana de preço é ~6,3%; o sistema assumia 10% de negociação.
- 1,6% dos anúncios tinham título e modelo contraditórios — agora fora da
  mediana.

**Margem sem inflar:** o alerta mostra três leituras (sobre o custo, sobre a
venda e um cenário conservador) e marca ⚠️ a margem acima de 60% — preço muito
baixo costuma ser defeito escondido, não pechincha.

**Precisão medida, não suposta:** cada alerta pode ser conferido no anúncio
real (aba *Oportunidades*); a aba *Resumo* mostra "X de Y alertas conferidos
eram reais" e por que os falsos falharam.

**Para BI:** a aba *Dados (BI)* e `scripts/exportar_bi.py` exportam CSVs
(fatos e dimensão) que abrem direto no Excel/Power BI.

**Limitações, de frente:** coleta num PC pessoal (cobertura irregular, medida
na tabela `coletas`), uma região, análises descritivas — ver
[`docs/COLETA_CONTINUA.md`](docs/COLETA_CONTINUA.md) e
[`docs/CASE_DATA_ANALYTICS.md`](docs/CASE_DATA_ANALYTICS.md).

<!-- PRINTS (adicionar quando capturados, em docs/img/):
![Alerta no Telegram](docs/img/telegram-alerta.png)
![Aba Resumo](docs/img/dashboard-resumo.png)
![Oportunidades](docs/img/dashboard-oportunidades.png)
-->

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

Os testes do pipeline novo (`tests/test_flow_prefect.py`, `tests/test_armazenamento_s3.py`)
são pulados sozinhos se o Prefect/boto3/moto não estiverem instalados; pra rodar
tudo, use o ambiente de `requirements-pipeline.txt` (seção abaixo).

## Pipeline (Prefect) e camadas

Além do caminho de sempre (`docker-compose up`, APScheduler), tem um segundo jeito
de rodar a mesma coleta: um flow do Prefect que também guarda o dado em camadas.
Decisões e trade-offs em
[`docs/ADR_001_pipeline_prefect_camadas.md`](docs/ADR_001_pipeline_prefect_camadas.md).

```
coletar ──► sanidade ──► diff ──► salvar bronze ──► transformar ──► alertar
(retry só       │        (banco        (JSON bruto)    (prata + ouro
 em erro de     │         do dashboard)                 + CSV pra BI)
 rede)          └─ reprovou: bruto vai pra quarentena/, nada é gravado
```

| Camada | O que guarda | Onde |
|---|---|---|
| Bronze | JSON bruto de cada coleta, como veio da OLX | `bronze/olx/iphone/uf=ES/data=.../coleta_*.json` |
| Prata | anúncio limpo, validado, sem duplicata e sem dado do vendedor | `prata/...` (JSON Lines) |
| Ouro | modelo estrela: `fato_anuncio_snapshot` + `dim_modelo`, `dim_local`, `dim_data` | `OURO_DB_PATH` (SQLite) + `ouro/*.csv` |

O lake grava numa pasta local por padrão (`LAKE_DIR`). Com `LAKE_BACKEND=s3`, grava
num bucket S3 via boto3 — MinIO local (`LAKE_S3_ENDPOINT_URL=http://localhost:9000`)
ou AWS, sem mudar código. Nenhuma credencial fica no repositório: tudo vem do `.env`.

**Rodar local (sem Prefect Cloud, sem conta):**

```bash
python -m venv .venv-pipeline
.venv-pipeline/Scripts/pip install -r requirements-pipeline.txt   # Linux/macOS: .venv-pipeline/bin/pip

# uma rodada (o Prefect sobe um servidor temporário sozinho)
PYTHONPATH=. LAKE_DIR=data/lake OURO_DB_PATH=data/ouro.db DB_PATH=data/olx_monitor.db \
  .venv-pipeline/Scripts/python flows/pipeline_olx.py

# agendado a cada SCRAPE_INTERVAL_MINUTES, com UI em http://127.0.0.1:4200
.venv-pipeline/Scripts/prefect server start                       # terminal 1
.venv-pipeline/Scripts/prefect config set PREFECT_API_URL=http://127.0.0.1:4200/api
PYTHONPATH=. .venv-pipeline/Scripts/python flows/pipeline_olx.py --agendar   # terminal 2

# consultas da ouro (LAG, ROW_NUMBER, mediana móvel de 7 dias)
PYTHONPATH=. OURO_DB_PATH=data/ouro.db python -m pipeline.consultas_ouro
```

MinIO é opcional e só sobe com o perfil: `docker-compose --profile lake up -d minio`
(preencha `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` no `.env` e confira a imagem — ver
o comentário no `docker-compose.yml`).

> Não rode o flow e o `docker-compose` do scraper ao mesmo tempo contra o mesmo
> banco: seriam duas coletas por intervalo, e a detecção de "anúncio sumiu" e o
> checkpoint de sanidade veriam rodadas em dobro.

Pra uma entrevista, separei as perguntas que eu esperaria sobre essa parte (e as
respostas com base no código) em
[`docs/perguntas_entrevista.md`](docs/perguntas_entrevista.md).

## Arquitetura

```
common/      -- config, schema (Pydantic) e storage (SQLite), compartilhados
scraper/     -- coleta: fetch -> parse -> sanidade -> diff -> save -> alerta
flows/       -- o mesmo pipeline orquestrado pelo Prefect (opcional)
pipeline/    -- camadas bronze/prata/ouro, armazenamento local/S3, SQL da ouro
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

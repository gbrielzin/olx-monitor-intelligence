# ADR 001 — Pipeline com Prefect, camadas bronze/prata/ouro e modelo estrela

**Data:** 12/11/2026 · **Status:** proposto (rodando local, ainda não substitui o caminho antigo)

## Contexto

O scraper roda há meses como um processo único (`scraper/main.py`) agendado pelo
APScheduler dentro do Docker: busca → parse → sanidade → grava → alerta. Funciona,
mas tem três buracos que eu queria fechar:

1. **Orquestração invisível.** Quando uma rodada falha, eu só fico sabendo pelo
   Telegram ou lendo log de container. Não tem histórico de execuções, nem retry:
   um 403 isolado da Cloudflare perde a rodada inteira. (Na coleta de teste de
   12/11/2026 isso aconteceu: a primeira requisição voltou 403 e a seguinte, segundos
   depois, voltou 200.)
2. **Não guardo o dado bruto.** O banco só tem o estado mais recente de cada anúncio
   (`anuncios`) e as quedas de preço (`historico_precos`). Se o parser tiver um bug,
   o dado daquela época está perdido — não dá pra reprocessar.
3. **Não dá pra ver o preço de um anúncio ao longo do tempo.** Como `anuncios` é
   upsert, a série "preço do anúncio X em cada coleta" não existe em lugar nenhum.

## Decisão

### 1. Prefect como orquestrador (`flows/pipeline_olx.py`)

Um flow com seis tasks — `coletar → sanidade → diff → salvar_bronze → transformar →
alertar` — reaproveitando o código do scraper (não copiei lógica: extraí o loop de
páginas pra `scraper/coleta.py` e o disparo de alertas pra `scraper/alertas.py`, e o
`main.py` antigo passou a usar os mesmos módulos).

- **Retry só onde faz sentido:** `coletar` tenta de novo 2 vezes (depois de 1 min e de
  5 min), e só quando o erro é de rede/HTTP (`FetchError`). Se o JSON de anúncios
  sumiu do HTML (`ValueError` do parser), não adianta tentar de novo — o front-end da
  OLX não muda em 1 minuto, e insistir só aumenta a pegada do scraper.
- **Falha visível:** qualquer task que falhe de vez deixa o flow em `Failed` na UI do
  Prefect, com o log da task. O hook `on_failure` manda aviso pro meu chat pessoal do
  Telegram (nunca pra grupo público).
- **Sanidade reprovada não some:** o bruto vai pra `quarentena/` no lake e a rodada
  para sem gravar nada no banco — mesmo princípio de "fail loud" do checkpoint antigo,
  mas agora eu fico com o HTML-que-virou-JSON pra investigar.

**Por que Prefect e não Airflow:**

| Critério | Prefect 3 | Airflow |
|---|---|---|
| Rodar no meu PC sem infraestrutura | `pip install prefect` e rodar o script; ele sobe um servidor temporário sozinho | Precisa de scheduler + webserver + banco de metadados (Postgres recomendado) — normalmente via docker-compose com vários serviços |
| Como escrevo o pipeline | Funções Python com decorator (`@flow`, `@task`); o flow é código normal, chamável num teste | DAG declarado em arquivo próprio, com operadores; testar exige mais estrutura |
| Testar no pytest | `prefect_test_harness()` e chamar a função | Possível, mas mais pesado |
| Mercado | Menor | Muito mais usado em vaga de dados — é o que eu mais vejo pedido |

Pro tamanho deste projeto (1 pipeline, 1 máquina, rodando a cada 12 min), o Airflow
seria mais infraestrutura do que pipeline. O Prefect me deu retry, histórico, logs e UI
com uma dependência só. O trade-off que eu aceito: Airflow é o padrão de mercado e eu
ainda não tenho prática nele — os conceitos (DAG, task, retry, idempotência, backfill)
são os mesmos, então é o próximo passo natural se eu precisar.

**Por que não substituí o APScheduler:** o caminho antigo (`docker-compose up`) está
rodando e alimentando o dashboard. O flow é um caminho paralelo, com seu próprio
ambiente (`requirements-pipeline.txt`), porque o Prefect 3 exige `pydantic >= 2.10` e
o scraper fixa `pydantic==2.9.2` no Docker. **Não rodo os dois ao mesmo tempo** contra
o mesmo banco: cada um faria sua coleta, e a detecção de "anúncio sumiu" e a média do
checkpoint de sanidade veriam rodadas em dobro.

### 2. Camadas bronze / prata / ouro (`pipeline/camadas.py`, `pipeline/ouro.py`)

| Camada | O que é | Formato | Onde |
|---|---|---|---|
| Bronze | JSON bruto de cada coleta, exatamente como saiu do HTML + metadados (URL, hora, contagens) | JSON | `bronze/olx/iphone/uf=ES/data=AAAA-MM-DD/coleta_<carimbo>.json` |
| Prata | Anúncio limpo: validado pelo schema Pydantic, sem propaganda, deduplicado por `listing_id` na coleta, com flag `confiavel` (mesma régua da mediana: `margem_e_confiavel`) | JSON Lines | `prata/...` (mesmo caminho da bronze) |
| Ouro | Modelo estrela pronto pra análise | SQLite (`OURO_DB_PATH`) + CSVs em `ouro/` no lake | ver item 4 |

Decisões que valem citar:

- **A prata é determinística.** O `coleta_em` vem dos metadados da bronze, não do
  relógio. Reprocessar a mesma bronze gera exatamente a mesma prata — é isso que torna
  seguro reprocessar depois de corrigir um bug no schema.
- **Dado pessoal fica só na bronze.** O JSON bruto traz o nome do vendedor (`olxPay`).
  A prata não carrega isso: pra análise de preço não serve, e assim o que eu exporto
  pra BI não tem dado pessoal. A bronze fica em `data/` (no `.gitignore`), nunca no
  GitHub.
- **Partição estilo Hive** (`uf=ES/data=...`): é o formato que Athena, Spark e DuckDB
  leem direto se isso um dia for pra nuvem.
- **Ouro idempotente.** Fato com `PRIMARY KEY (listing_id, plataforma, coleta_em)` e
  `INSERT OR IGNORE`; dimensões com `UNIQUE` na chave natural. Carregar a mesma prata
  duas vezes não duplica nada, e `reconstruir_do_lake()` refaz a ouro inteira do zero
  a partir da prata.

### 3. Armazenamento com interface de S3 (`pipeline/armazenamento.py`)

Uma interface pequena (`gravar`, `ler`, `listar`, `existe`) com duas implementações:

- **`ArmazenamentoLocal`** (padrão): grava numa pasta. Roda sem Docker, sem conta,
  sem credencial. Grava em arquivo temporário e renomeia, pra não deixar JSON pela
  metade se o processo cair; e recusa chave com `..` (não escreve fora do lake).
- **`ArmazenamentoS3`**: boto3 com `endpoint_url` configurável. Apontando pro MinIO
  local, é S3 sem sair do PC; sem `endpoint_url`, fala com a AWS. **O código do
  pipeline não muda** — só o `.env` (`LAKE_BACKEND=s3`).

**Por que MinIO local (e não AWS direto):** eu não tenho conta AWS paga e não quero
correr risco de cobrança nem de vazar chave enquanto aprendo. O MinIO fala a mesma API
do S3, então o mesmo `boto3.put_object` que roda contra ele rodaria contra a AWS. O
trade-off: MinIO local não me ensina IAM, política de bucket, custo por requisição nem
classes de armazenamento — isso só a AWS de verdade ensina. Por isso deixei o fallback
em pasta local como padrão (zero dependência) e o MinIO como opção
(`docker-compose --profile lake up -d minio`).

Os testes do S3 usam `moto` (S3 falso em memória): mesmo contrato do backend local,
sem rede e sem credencial real.

**Ressalva honesta:** em 12/11/2026 eu não consegui subir o MinIO — o Docker estava
parado, e a imagem oficial `minio/minio` não aparecia mais no Docker Hub. Por isso a
imagem do serviço `minio` no `docker-compose.yml` é configurável (`MINIO_IMAGE`) e
precisa ser conferida antes do primeiro uso. O backend S3 foi testado só com `moto`.

### 4. Modelo estrela na ouro (`pipeline/sql/ouro_sqlite.sql`)

```
              dim_data (data_key AAAAMMDD)
                     │
dim_modelo ── fato_anuncio_snapshot ── dim_local
(categoria,    (1 linha por anúncio      (uf, município)
 modelo, GB)    POR COLETA: preço)
```

- **Grão do fato:** um anúncio numa coleta. É o que a tabela operacional não tem — a
  série de preço de cada anúncio no tempo.
- **Chaves:** surrogate key inteira nas dimensões (`modelo_key`, `local_key`), chave
  natural `UNIQUE` (`'iphone|IPHONE 13|128'`, `'ES|Vitória'`) pra carga idempotente;
  `data_key` no formato `AAAAMMDD`, padrão de data warehouse. Chave estrangeira
  cobrada (`PRAGMA foreign_keys = ON`).
- **`dim_modelo` sem UF:** a UF fica em `dim_local`, pra comparar o mesmo modelo entre
  estados — diferente do `grupo` do sistema, que junta UF + modelo.
- **Dia de Brasília** (UTC−3 fixo) em `dim_data`: uma coleta às 01h UTC é 22h do dia
  anterior pra quem anuncia. Atenção: `medianas_diarias` do banco operacional usa o dia
  em UTC, então os dois podem divergir em coletas entre 21h e 0h.
- **Índices** pelas consultas: `(modelo_key, data_key)` pra série por modelo,
  `local_key` e `coleta_em`.
- DDL de Postgres de referência em `pipeline/sql/ouro_postgres.sql` (IDENTITY,
  TIMESTAMPTZ, NUMERIC) — **escrito, ainda não executado contra um Postgres**.

Consultas com window function (`pipeline/consultas_ouro.py`), cada uma respondendo uma
pergunta do projeto:

| Consulta | Pergunta | Window function |
|---|---|---|
| `quedas_de_preco` | Quais anúncios baixaram de preço de uma coleta pra outra, e quanto? | `LAG(preco) OVER (PARTITION BY listing_id ORDER BY coleta_em)` |
| `mais_baratos_por_modelo` | Na última coleta de cada UF, quais os 3 mais baratos de cada modelo (só confiáveis)? | `ROW_NUMBER()` e `COUNT(*) OVER (PARTITION BY uf, modelo)` |
| `mediana_movel_7d` | Como a mediana diária de cada modelo anda no tempo? | mediana via `ROW_NUMBER` + `COUNT`, média móvel com `RANGE BETWEEN 6 PRECEDING` sobre `julianday` |

Dois detalhes da mediana móvel que mudam o número:
- **1 preço por anúncio por dia** (o último do dia). Um anúncio parado no ar aparece em
  dezenas de coletas por dia; sem isso, ele pesaria mais que um anúncio novo — o mesmo
  viés que o upsert do banco operacional já evita.
- **7 dias corridos, não 7 linhas.** A coleta roda num PC pessoal e tem buraco de dias;
  com `ROWS`, "7 linhas" poderia cobrir três semanas sem ninguém perceber. A consulta
  devolve `dias_com_dado_na_janela` pra deixar isso visível.

## Consequências

- **Positivas:** histórico de execuções e retry; dado bruto reprocessável; série de
  preço por anúncio; modelo pronto pra BI; mesmo código pra disco local, MinIO ou S3.
- **Negativas:** dois caminhos de execução pra manter (APScheduler e Prefect) até eu
  decidir migrar; dois ambientes Python (pino de pydantic); a ouro começa vazia — não dá
  pra reconstruir snapshots do passado, porque eles nunca foram guardados.
- **Não testado ainda:** MinIO de verdade (Docker parado), DDL Postgres, modo agendado
  (`--agendar`, que precisa de `prefect server start`), e Python 3.11 — os testes desta
  mudança rodaram em Python 3.14.7.

## Próximos passos

1. Subir o MinIO (conferindo a imagem) e rodar o flow com `LAKE_BACKEND=s3`.
2. Decidir se o Prefect substitui o APScheduler no Docker (aí entra um serviço
   `pipeline` no compose com `requirements-pipeline.txt`).
3. Ligar o Power BI/Tableau nos CSVs de `ouro/`.
4. Quando tiver conta AWS: mesmo código, `LAKE_S3_ENDPOINT_URL` vazio, credencial por
   perfil da AWS (nunca em arquivo do repositório).

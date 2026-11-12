# Perguntas de entrevista — pipeline, camadas e modelagem

Perguntas que eu esperaria de um entrevistador sobre o pipeline com Prefect, as
camadas bronze/prata/ouro e o modelo estrela — com a resposta que eu dou, baseada
no código que está no repositório (e não no que eu gostaria que estivesse).
Contexto das decisões: [`ADR_001_pipeline_prefect_camadas.md`](ADR_001_pipeline_prefect_camadas.md).

---

### 1. Por que Prefect e não Airflow?

Pelo tamanho do problema. É um pipeline, numa máquina, a cada 12 minutos. O Prefect
roda com `pip install` e um script Python — ele sobe um servidor temporário sozinho
na primeira execução. O Airflow pediria scheduler, webserver e banco de metadados só
pra isso. Eu sei que o Airflow é mais pedido em vaga; os conceitos que usei (task,
retry, estado de falha, idempotência, backfill) são os mesmos, então não é
conhecimento jogado fora. Se o projeto crescesse pra vários pipelines dependentes
entre si, eu reavaliaria.

### 2. O que acontece se a OLX devolver 403?

A task `coletar` tem `retries=2` com espera de 60 s e depois 300 s, mas com uma
condição (`_so_falha_de_rede`): só tenta de novo se o erro for `FetchError` (rede/HTTP).
Isso não é burlar bloqueio — não troco IP, não troco user-agent, não uso proxy. É a
mesma requisição, mais tarde. Se as três falharem, o flow termina `Failed` e o hook
`on_failure` me avisa no Telegram pessoal. Teste: `test_falha_de_rede_tenta_de_novo`.
Na coleta de teste de 12/11/2026 eu vi exatamente esse caso: um 403 seguido de um 200
segundos depois.

### 3. E se o parser quebrar porque a OLX mudou o site?

`extract_ads` levanta `ValueError` quando não acha o JSON de anúncios no HTML. Nesse
caso **não** tem retry — o HTML não vai mudar em um minuto, insistir só aumenta a
pegada (teste `test_parser_quebrado_nao_tenta_de_novo`). E aqui tem um bug que eu
achei fazendo essa refatoração: no `main.py` antigo, o `except ValueError` estava em
volta do `build_ads`, que nunca levanta esse erro. Então, se o parser quebrasse, a
exceção escapava da rodada sem o aviso de "possivelmente quebrado" no Telegram.
Corrigi e travei com `test_rodar_coleta_avisa_quando_o_json_de_anuncios_some`.

### 4. Qual a diferença entre bronze, prata e ouro no seu projeto?

- **Bronze:** o JSON bruto de cada coleta, sem tocar — inclusive propaganda e
  duplicata. Serve pra reprocessar se eu achar bug no parser ou no schema.
- **Prata:** anúncio validado pelo mesmo schema Pydantic do scraper, sem propaganda,
  deduplicado por `listing_id` dentro da coleta, sem nome de vendedor, com a flag
  `confiavel` (mesma regra que decide quem entra na mediana do sistema).
- **Ouro:** modelo estrela pronto pra consulta e BI.

Se a sanidade reprovar, o bruto vai pra `quarentena/` em vez de `bronze/`, e nada
segue pra prata/ouro.

### 5. Se você rodar a transformação duas vezes, duplica dado?

Não. A prata é determinística: o `coleta_em` vem dos metadados da bronze, não do
relógio — a mesma bronze sempre gera a mesma prata. Na ouro, o fato tem
`PRIMARY KEY (listing_id, plataforma, coleta_em)` com `INSERT OR IGNORE`, e as
dimensões têm `UNIQUE` na chave natural. Carregar duas vezes devolve 0 linhas novas
(`test_carga_e_idempotente`), e `reconstruir_do_lake()` refaz a ouro inteira a partir
da prata.

### 6. Qual o grão da sua tabela fato? Por que não usar a tabela `anuncios`?

Uma linha por anúncio **por coleta**. A tabela operacional `anuncios` é upsert: guarda
só o estado mais recente de cada anúncio. Ela é ótima pra "o que está no ar agora",
mas não responde "qual era o preço desse anúncio na terça". O fato snapshot responde.
O custo é volume: cada coleta grava de novo todo anúncio que continua no ar.

### 7. Como você calcula mediana no SQLite, que não tem `MEDIAN()`?

Com window function: `ROW_NUMBER()` ordenando os preços e `COUNT(*)` do grupo, e
pego as posições `(n+1)/2` e `(n+2)/2` — com divisão inteira, isso dá o elemento do
meio quando n é ímpar e os dois do meio quando é par; a média deles é a mediana. Tem
teste pros dois casos. No Postgres seria só `percentile_cont(0.5)`.

### 8. Por que a média móvel usa `RANGE` e não `ROWS`?

Porque a coleta tem buraco: roda no meu PC, que às vezes fica dias desligado. Com
`ROWS BETWEEN 6 PRECEDING`, "7 linhas" podem cobrir três semanas. Com
`RANGE BETWEEN 6 PRECEDING` sobre `julianday(data)`, são 7 dias corridos de verdade.
A consulta ainda devolve `dias_com_dado_na_janela`, pra quem lê saber quantos dias
de fato entraram na média (`test_media_movel_usa_7_dias_corridos_mesmo_com_buraco_de_coleta`).

### 9. Um anúncio que fica no ar o dia todo aparece em ~100 coletas. Isso não distorce a mediana do dia?

Distorceria, e por isso a consulta pega **um preço por anúncio por dia** (o último do
dia) antes de calcular a mediana. É o mesmo cuidado que o banco operacional já tinha:
lá, o upsert existe justamente porque o schema antigo, com uma linha por rodada,
fazia anúncio parado pesar mais na mediana. Teste:
`test_mediana_diaria_usa_um_preco_por_anuncio_por_dia`.

### 10. Por que MinIO e não S3 da AWS? Funciona na AWS?

Não tenho conta AWS paga e não queria risco de cobrança nem de vazar chave enquanto
aprendo. O MinIO fala a API do S3; o código usa `boto3` com `endpoint_url`
configurável. Pra ir pra AWS, deixo `LAKE_S3_ENDPOINT_URL` vazio e uso a credencial
padrão da AWS (perfil/variável de ambiente) — o código não muda. Sendo honesto sobre
o que está testado: o backend S3 foi testado com `moto` (S3 falso em memória). O
MinIO em si eu ainda não subi — o Docker estava parado quando fiz isso. E o padrão é
gravar numa pasta local, que não depende de nada.

### 11. Onde ficam as credenciais?

Só no `.env`, que está no `.gitignore`. O `.env.example` tem placeholders vazios ou
"troque-este-usuario". Se as chaves do S3 ficarem vazias, o boto3 usa a cadeia padrão
de credenciais da AWS. Os testes usam credencial falsa definida só durante o teste.

### 12. Como você garante que o flow novo não quebrou o caminho antigo?

Não copiei lógica: extraí o loop de páginas pra `scraper/coleta.py` e o disparo de
alertas pra `scraper/alertas.py`, e o `main.py` antigo passou a chamar os mesmos
módulos. A suíte antiga (160 testes) continua passando, e o `docker-compose.yml` sem
perfil continua subindo só scraper + dashboard. O flow tem ambiente próprio
(`requirements-pipeline.txt`) porque o Prefect 3 precisa de `pydantic >= 2.10` e o
scraper fixa 2.9.2 no Docker.

### 13. Por que as datas da ouro são em horário de Brasília?

Porque a pergunta é "como o preço anda por dia" na visão de quem anuncia no ES. Uma
coleta às 01h UTC é 22h do dia anterior aqui. Uso UTC−3 fixo (sem horário de verão
desde 2019) — e documentei que o `medianas_diarias` do banco operacional usa o dia em
UTC, então os dois podem divergir em coletas entre 21h e 0h.

### 14. O que você faria diferente com mais tempo?

- Subir o MinIO e rodar o flow contra ele.
- Decidir se o Prefect substitui o APScheduler no Docker, em vez de manter os dois.
- Testar o DDL de Postgres de verdade (hoje está escrito, não executado).
- Rodar a suíte em Python 3.11 (a versão do Docker); estes testes rodaram em 3.14.
- Ligar Power BI/Tableau nos CSVs da camada ouro.

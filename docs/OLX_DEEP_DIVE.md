# OLX Monitor Intelligence — Deep Dive Técnico

> **Propósito deste documento:** documentação técnica em profundidade do projeto,
> camada por camada (o quê → como → por quê → trade-off → o que quebra → como
> escalar) — cobre arquitetura, scraping, banco de dados, backend, Docker,
> interface e qualidade.
>
> **Metodologia:** toda afirmação de "por quê" vem de uma fonte verificável —
> comentário no código, mensagem de commit, teste, README, ou uma conversa
> anterior registrada (datada). Quando a razão de uma decisão não está registrada
> em nenhum lugar, o documento diz isso explicitamente em vez de apresentar uma
> justificativa especulativa como fato. Todo número vem de uma consulta real (ao
> código, ao git, ou ao banco de dados) — nunca é estimativa apresentada como
> dado. Datas de "hoje"/"ao vivo" abaixo se referem a **02/09/2026**, quando a
> versão original deste documento foi escrita; números de uma auditoria anterior
> vêm marcados com a data dela (25–26/08/2026). **Revisado em 15/09/2026** pra
> reconciliar com o commit `80e4585` (12/09/2026), que descontinuou monitor
> gamer e computador completo do agendamento — ver nota em cada seção afetada.
>
> Referências de código usam o formato `arquivo:linha` — abra o arquivo no VS Code
> e vá direto na linha citada.

---

## 1. Problema

**O que o projeto resolve.** É um sistema pessoal de arbitragem informacional:
monitora anúncios de usados na OLX, calcula o preço justo de mercado (mediana)
de cada subgrupo de produto, e avisa via Telegram quando um anúncio aparece —
ou cai de preço — abaixo desse preço justo, dentro de um orçamento que faz
sentido comprar. O objetivo final é comprar abaixo do valor de mercado e
revender com margem (a aba "Vendas" do dashboard é onde isso é registrado
manualmente).

**Categorias: histórico e estado atual.** O MVP testou três categorias em
paralelo — **monitor gamer, iPhone e computador completo** — deliberadamente,
período descrito internamente como "dado amplo antes de regra de negócio por
categoria". Depois de semanas rodando as três, o commit `80e4585`
(`remocao-de-computadores-e-monitores`, 12/09/2026) parou de agendar monitor e
computador, registrando o motivo direto no código (`common/config.py`):
*"mercado se mostrou ineficaz"*. **Hoje (revisão de 15/09/2026), só iPhone
continua ativo — e não mais só no Espírito Santo: `scraper/main.py` roda uma
rodada por região configurada em `settings.iphone_regioes` (modelo `Regiao`),
sequencialmente, cada uma podendo notificar um chat de Telegram diferente.**
O código de monitor/computador não foi apagado (`rodar_coleta_monitor`/
`rodar_coleta_computador` em `scraper/main.py` continuam definidas, só não são
mais chamadas pelo agendador) — dado histórico das duas categorias permanece no
banco pra análise. As seções abaixo que descrevem o comportamento *atual* do
sistema (agendamento, arquitetura ao vivo) refletem só iPhone; onde este
documento cita monitor/computador com números, é sempre uma referência ao
período em que as três categorias estavam ativas, marcada como tal.

A tese, resumida no próprio README (`../README.md:3-7`): um sistema de
arbitragem informacional que calcula o preço justo de mercado (mediana por
grupo comparável) e avisa quando um anúncio aparece — ou cai de preço —
abaixo dele. A ideia por trás: parte do mercado de usados é ineficiente —
vendedor urgente ou desinformado anuncia abaixo do preço justo — e achar
isso manualmente, na hora certa, não escala; um scraper de baixa frequência
sim.

**Por que scraping, e não uma API.** Não há indício, nem no código nem no
histórico do projeto, de uma API pública da OLX pra busca de anúncios — o único
caminho viável pra observar o catálogo em tempo real de forma automatizada é ler
o HTML que o site já serve publicamente. A OLX renderiza os anúncios no servidor
e embute os dados como JSON dentro do próprio HTML (ver seção 3) — isso é o que
torna scraping via HTTP simples viável aqui, sem precisar de um browser
automatizado.

**A dificuldade real de achar boas oportunidades manualmente** — isto é o que a
auditoria de 25–26/08/2026 ("Raio-X do Monitor Gamer", o relatório que motivou
boa parte das regras de negócio hoje em `common/config.py`) mediu com dado real,
não é uma afirmação genérica de pitch. Os cinco pontos abaixo são desse período
(quando as três categorias estavam ativas em paralelo — ver nota acima sobre o
estado atual):

1. **Volume que não escala pra revisão manual.** Só de monitor, o catálogo ativo
   girava em ~250 anúncios naquela auditoria (hoje, 02/09/2026, são 500 — ver
   seção 3.3). Atualizando o tempo todo, em 3 categorias.
2. **"Barato" não é sinônimo de "bom negócio".** Na faixa R$0–300 de monitor,
   **22% dos anúncios (16 de 74) já vinham marcados "Com defeito ou avarias"**
   — dado medido na mesma auditoria. Sem esse filtro, metade do trabalho de
   garimpar manualmente é descartar sucata disfarçada de pechincha.
3. **O preço justo muda de régua por segmento.** Não dá pra comparar um monitor
   com outro só pela marca — Hz, tipo e marca importam; pra iPhone, marca é
   sempre "Apple" (não discrimina nada), o que importa é modelo+armazenamento;
   pra computador, quase metade dos anúncios vem com marca "Outros" (montagem
   avulsa) — o que separa um PC de R$800 de um de R$3000 é CPU+RAM, não a marca
   (ver `common/schema.py`, propriedade `grupo` de cada categoria).
4. **A janela de oportunidade fecha rápido.** A mesma auditoria mediu, com o
   pouco histórico que existia então (42 casos, 3 dias), que um anúncio some do
   ar em média 15,7h depois de listado (variando de 0 a 68h) — sinal de que
   contar com "eu vi por acaso navegando" não é uma estratégia confiável.
5. **Até a comparação automática engana se for ingênua.** O próprio sistema, na
   primeira versão, comparava o preço de um anúncio "com defeito" contra a
   mediana de unidades funcionando e mostrava margem de até 872% — não existia
   de verdade. Julgamento manual rápido corre o mesmo risco de erro sistemático.

---

## 2. Arquitetura

### 2.1 Visão geral

```
common/      -- config (pydantic-settings), schema (Pydantic), storage (SQLite) — compartilhado
scraper/     -- coleta: fetch -> parse -> sanidade -> upsert -> diff -> avaliação -> alerta
dashboard/   -- Streamlit, leitura do banco (+ 1 escrita manual — ver 2.3)
data/        -- banco SQLite (gitignored, criado em runtime, montado como volume Docker)
```

Dois serviços Docker (`docker-compose.yml`), cada um com seu próprio
`Dockerfile` e `requirements.txt`, ambos montando o mesmo volume `./data:/data`:

- **`scraper`** — processo de longa duração, sem porta exposta, `restart:
  unless-stopped`. Roda um `BlockingScheduler` (APScheduler) com dois jobs:
  `rodar_coleta_iphone_todas_regioes` a cada `scrape_interval_minutes` (12 min)
  — uma rodada sequencial por região em `settings.iphone_regioes`, com um
  delay pequeno entre regiões (`intervalo_entre_regioes_segundos`) — e
  `enviar_resumo_diario` (cron, 1x/dia, opcional). O agendamento de
  monitor/computador foi removido do scheduler em 12/09/2026 (ver seção 1);
  as funções (`rodar_coleta_monitor`/`rodar_coleta_computador`) continuam
  definidas em `scraper/main.py`, só não são mais chamadas.
- **`dashboard`** — Streamlit, porta `8501` exposta, `depends_on: scraper`
  (controla só a ordem de subida do container, não prontidão — ver seção 7).

Não existe um terceiro serviço de "backend"/API — o scraper *é* o backend, só
que não recebe requisições: ele roda em ciclo, agendado.

### 2.2 Fluxo completo dos dados

```
   APScheduler (tick a cada 12 min -- 1 rodada por região de iPhone
                configurada em settings.iphone_regioes, sequencial)
        |
        v
   fetch_html() ---> extract_ads() ---> build_ads() ---> checar_sanidade()
   (requests)        (JSON embutido      (valida com         |
                       no HTML)           Pydantic)      falhou? -> Telegram
                                               |          + aborta a rodada
                                               v          (nada é gravado)
                                          upsert_ads()
                                        (SQLite, upsert
                                         por listing_id)
                                               |
                                               v
                                      separar_novidades()
                                      (novos / com queda)
                                               |
                                               v
                                          avaliar() por anúncio
                                     (mediana do grupo, orçamento)
                                               |
                                        é oportunidade?
                                               |
                                               v
                                       enviar_telegram()

   ------------------------------------------------------------
   Em paralelo, a qualquer momento, sem depender do ciclo acima:

   Browser --GET--> Streamlit (dashboard/app.py)
                        |
                        v
              queries.carregar_ativos() (pd.read_sql_query
              no MESMO arquivo SQLite, modo WAL)
                        |
                        v
              stats.medianas_todos_grupos() / avaliar_preco()
                        |
                        v
              st.dataframe / st.metric / plotly (charts.py)
```

O ponto que sustenta essa arquitetura: **scraper e dashboard nunca se falam
diretamente** — toda comunicação entre os dois passa pelo arquivo SQLite em
`data/`. Isso só funciona sem erro de "database is locked" por causa do modo
WAL (`common/storage.py:1-5` — ver seção 4.5).

### 2.3 Uma imprecisão real no README, vale registrar

O próprio `../README.md:54` descreve `dashboard/` como **"só leitura do mesmo
banco"**. Isso não é 100% exato: `dashboard/vendas.py` executa `INSERT`/`UPDATE`
na tabela `vendas` a partir do próprio processo Streamlit, quando o usuário
registra uma compra (`registrar_compra`) ou marca como vendida
(`marcar_como_vendido`). É uma escrita manual, pontual, disparada por ação
humana — nunca concorrente com o scraper (que nunca toca a tabela `vendas`) —
então não há conflito real com o motivo de existir o modo WAL (viabilizar 1
escritor de alta frequência + N leitores). Mas, tecnicamente, "só leitura" está
incorreto. Detalhe pequeno, mas é o tipo de coisa que um entrevistador que leu o
README E o código pode perguntar — melhor você já saber que o README simplificou
aqui.

---

## 3. Scraping

### 3.1 Como o projeto acessa/coleta os anúncios

`scraper/fetcher.py` usa `requests.Session()` reaproveitada entre chamadas (não
uma requisição isolada por vez), com headers fixos (`User-Agent` de Chrome
desktop, `Accept-Language: pt-BR`) e timeout de 15s (`request_timeout_seconds`,
`common/config.py:45`). Não há Playwright, Selenium ou qualquer browser
automatizado.

**Por quê `requests` e não um browser headless** — isto está documentado
explicitamente, tanto no README (`../README.md:58-63`) quanto no docstring do
próprio módulo (`scraper/fetcher.py:1-14`): a OLX **renderiza os anúncios no
servidor** e embute os dados como JSON dentro do HTML (o payload de streaming
do Next.js/React Server Components) — então não é preciso executar JavaScript
nenhum pra ler os dados, só pegar o HTML como ele chega. Isso é mais leve, mais
rápido, e deixa uma pegada bem menor no site do que abrir um Chromium a cada
rodada.

### 3.2 Como identifica as informações — extração do JSON embutido

`scraper/parser.py` é a peça mais delicada do projeto. O HTML carrega blocos
`self.__next_f.push(...)` (protocolo de streaming do Next.js) e, em algum ponto
desse texto, existe `"ads":[...]` com a lista completa de anúncios da página —
mas escapado como string JavaScript (`\"ads\":[...]`, não `"ads":[...]` puro).

O docstring do arquivo (`scraper/parser.py:1-20`) documenta duas armadilhas
reais, as duas cobertas no código:

1. **Um regex guloso do tipo `"ads":\[(.*?)\]` quebra** — títulos de anúncio às
   vezes têm colchete dentro (ex: `"Monitor [PROMOÇÃO]"`). A extração
   (`extract_ads`, `scraper/parser.py:44-81`) por isso **conta profundidade de
   colchete manualmente**, caractere a caractere, rastreando se está dentro de
   uma string (pra ignorar colchetes/aspas escapados) — não usa regex pra achar
   o fim do array.
2. **Mesmo com a extração correta, o texto ainda carrega escape de string JS**
   (`\"`). Em vez de reimplementar regras de escape na mão, o código embrulha o
   trecho em aspas e deixa o próprio `json.loads()` desfazer o escape
   (`_try_unescape`, `scraper/parser.py:35-41`) — reaproveita o decoder de
   string do `json`, não inventa um novo.

Cada item bruto vira um `MonitorAd`/`IphoneAd`/`ComputadorAd` (Pydantic) via
`.from_olx_json()`, que lê o dicionário `properties` (uma lista de
`{name, value}`, convertida pra dict) mais os campos de topo (`listId`,
`subject`, `priceValue`, `locationDetails`, `olxPay`). Um item sem `listId` é
descartado silenciosamente — é um placeholder de propaganda misturado no array,
não um anúncio (`scraper/parser.py:101-102`).

Dentro de cada schema, há um padrão que se repete 3 vezes (recuperação de dado
faltante a partir do título via regex) porque a OLX frequentemente não tem o
campo estruturado preenchido:

| Campo | Categoria | Regra | Achado real que motivou |
|---|---|---|---|
| `marca` | Monitor | Se vier `None`/"Outros", procura entre 20 marcas conhecidas no título, com borda de palavra (`\b`) pra não casar "LG" dentro de "algo" | 140 de 292 anúncios (48%) vinham "Outros" (auditoria 25–26/08) |
| `cpu_modelo` | Computador | Se faltar, procura entre 13 padrões (i3/i5/i7/i9, Ryzen 3/5/7/9, Xeon, Celeron, Pentium, Core 2 Duo) | 19 de 250 anúncios (7,6%) sem CPU estruturada — corrigido, 13 recuperados do título (commit `c6da790`) |
| `ram_gb` | Computador | Se faltar, procura número colado a um marcador (`ram`/`ddr\d`/`memória`) via *lookahead* — não consome o marcador, então um número rejeitado libera o marcador pro próximo candidato | ~20% dos anúncios sem RAM estruturada; achado real: "SSD 120GB RAM 6GB" um regex ingênuo capturava 120 (do SSD), não 6 (commit `fe8f1bf`) |

Em todos os três casos, o campo estruturado da OLX (quando existe) **sempre tem
prioridade** sobre o que foi extraído do título — o título só é usado como
fallback, nunca sobrescreve um dado bom (testado explicitamente em
`tests/test_schema.py`, ex.: `test_marca_estruturada_tem_prioridade_sobre_o_titulo`).

### 3.3 Paginação

`scraper/fetcher.py:43-47` (`url_pagina`) monta `?o=N` (ou `&o=N` se já existir
query string) pra cada página. O loop de paginação vive em
`scraper/main.py:83-91`, dentro de `rodar_coleta()`:

```python
for pagina in range(1, settings.max_paginas + 1):
    if pagina > 1:
        time.sleep(1)
    html = fetch_html(url_pagina(search_url, pagina))
    pagina_raw = extract_ads(html)
    reais = [i for i in pagina_raw if "listId" in i]
    raw_items.extend(pagina_raw)
    if len(reais) < 50:
        break  # última página de resultado real
```

Para na primeira página que traz menos de 50 itens reais (a OLX pagina em
blocos de 50) — condição de parada natural, sem precisar saber de antemão
quantos anúncios existem. `max_paginas=10` é o teto duro (até 500
anúncios/categoria/rodada); 1s de espera entre páginas dentro da mesma rodada.

**Por que 10, não outro número** — isto tem uma história real, em duas partes:

- O teto **era 5** (250 anúncios/categoria). O banco mostrava `total_anuncios`
  = exatamente 250 em dezenas de rodadas seguidas, nas 3 categorias — sinal de
  que o teto cortava a coleta *antes* do loop achar uma página curta de
  verdade, escondendo parte do mercado e marcando anúncio empurrado pra fora
  da janela como "sumiço" falso (commit `c553b83`, `common/config.py:35-39`).
- Perguntado explicitamente sobre como corrigir isso (27/08/2026), o usuário
  escolheu a opção **"moderada" (5→10, dobra requisições)** em vez de
  "generosa/sem teto" (5→20) ou deixar como está — alinhado com a filosofia
  que o próprio README já documentava, de scraper "deliberadamente discreto".

**Observação ao vivo de 02/09/2026 (histórica):** as últimas rodadas das 3
categorias, então todas ativas, estavam batendo `total_anuncios = 500` de forma
consistente e exata (conferido direto na tabela `coletas`) — o mesmo padrão
("número redondo e constante") que motivou subir de 5 para 10 páginas antes.
Na época, isso não estava diagnosticado: podia ser coincidência de o mercado
real ter ~500+ anúncios ativos, ou o teto cortando de novo.

**O que aconteceu depois:** o commit `80e4585` (12/09/2026) descontinuou
monitor e computador citando "mercado se mostrou ineficaz" (ver seção 1) — o
commit não referencia esta observação especificamente, então não há como
afirmar com certeza que o teto de paginação foi a causa raiz da leitura
"mercado ineficaz". O que dá pra afirmar, com fonte verificável: a suspeita
levantada aqui nunca foi formalmente investigada antes da categoria ser
descontinuada — permanece como um "não sei" documentado, não uma explicação
fechada.

### 3.4 Tratamento de erros

Três níveis, cada um isolado, cada um "fail loud" (avisa e desiste da rodada
daquela categoria, sem afetar as outras 2 nem a próxima rodada):

| Falha | Onde é pega | O que acontece |
|---|---|---|
| Rede (timeout, DNS, HTTP erro) | `FetchError` em `fetcher.py`, capturada em `main.py:92-95` | Log de erro + Telegram "falha ao buscar" + aborta a rodada |
| Site mudou (chave `"ads"` sumiu do HTML, ou array não fechou) | `ValueError` em `parser.py`, capturada em `main.py:99-105` | Log + Telegram "possivelmente quebrado, a OLX pode ter mudado o front-end" + aborta |
| Item individual malformado (falta campo obrigatório) | `try/except Exception` por item, dentro de `build_ads()` (`parser.py:100-108`) | `logger.warning`, item descartado — **não derruba o lote inteiro** |
| Gravação no banco falha | `try/except Exception` em `main.py:114-119` | Log + Telegram "falha ao gravar" + aborta (não perde silenciosamente) |
| Falha ao notificar no Telegram | `try/except requests.RequestException` dentro do próprio `notifier.py:45-47` | Só loga erro — **não pode derrubar o scraper** por causa de uma notificação |

O checkpoint de sanidade (`scraper/sanity.py`) é uma camada a mais, depois do
parse e antes de gravar — ver seção 3.6.

### 3.5 Duplicatas

Resolvido em duas camadas:

1. **Schema:** `anuncios` tem `PRIMARY KEY (listing_id, plataforma)` e um
   `UNIQUE INDEX` sobre `url` (`common/storage.py:81,84`).
2. **Upsert, não insert:** `upsert_ads()` (`common/storage.py:476-637`) usa
   `INSERT ... ON CONFLICT (listing_id, plataforma) DO UPDATE SET ...` — o
   mesmo anúncio, visto em N rodadas seguidas com o mesmo preço, atualiza a
   **mesma linha** (`ultimo_visto_em` avança, o resto não muda), nunca gera
   linha nova. Só uma **queda real de preço** grava uma linha nova em
   `historico_precos`.

Isto não era assim na primeira versão do projeto: antes do primeiro commit sob
controle de versão, o schema gravava 1 linha por anúncio *por rodada de
coleta* — no banco real da época, isso eram **20.250 linhas para 292 anúncios
distintos (98,6% de redundância pura, sempre com o mesmo preço)**, distorcendo
a mediana usada pela camada de oportunidade (cada anúncio parado no ar pesava
N vezes, não 1). Corrigido com uma migração automática e retroativa — ver
seção 4.7.

**Terceira camada, achada em produção em 15/09/2026 (não coberta pelas duas
acima):** as duas camadas resolvem duplicata *entre* rodadas — o mesmo
anúncio visto em rodadas diferentes. Nenhuma delas cobria duplicata *dentro*
da mesma rodada: a paginação da OLX pode repetir um anúncio entre páginas
diferentes de uma única coleta (destaque reaparecendo, ou os resultados
mudando de ordem entre a requisição de uma página e a próxima, todas dentro
de poucos segundos). Reproduzido ao vivo contra a OLX: de 500 anúncios de
iPhone/ES coletados numa rodada, 3 vieram duplicados.

Antes do fix, isso gerava duas tentativas de `INSERT` em `historico_precos`
com a mesma chave primária `(listing_id, plataforma, registrado_em)` — a
mesma `momento` (timestamp) é compartilhada por todos os anúncios de uma
rodada (`common/storage.py:upsert_ads`, variável `momento`), então dois
registros do mesmo anúncio na mesma rodada colidem exatamente. A exceção
`sqlite3.IntegrityError` estourava **dentro da mesma transação** do upsert de
`anuncios` — como `get_connection()` só dá `commit()` se o bloco `with`
terminar sem exceção (`common/storage.py:174-184`), o rollback implícito
descartava a rodada inteira: nem os 497 anúncios sem duplicata eram
gravados. `scraper/main.py:rodar_coleta` captura a exceção, loga e manda um
alerta de erro pro Telegram — mas continua rodando (não crasha o processo),
o que tornava o sintoma fácil de não notar: parece só mais um "possivelmente
quebrado" no Telegram, não "essa rodada nunca existiu pro banco". É provável
que parte dos buracos de uptime medidos na seção 7.5 venha daqui, não só da
máquina hibernando — coleta com container "Up" e HTTP funcionando, mas
gravação falhando silenciosamente.

Corrigido deduplicando `ads` por `listing_id` logo no início de
`upsert_ads()`, antes de montar `upsert_rows`/`historico_rows` — a própria
docstring do módulo promete "uma linha por anúncio", e agora isso vale
também dentro de uma única rodada, não só entre rodadas. Teste de regressão:
`tests/test_storage.py::test_listing_duplicado_na_mesma_rodada_nao_quebra_a_rodada_inteira`.

### 3.6 Mudanças no site — o checkpoint de sanidade

`scraper/sanity.py` roda depois do parse e antes de qualquer gravação. Três
checagens, todas precisam passar:

```python
def checar_sanidade(ads, media_historica) -> SanityResult:
    # 1. zero anúncios extraídos -> provável quebra total do parser
    # 2. menos de min_price_field_ratio (70%) dos anúncios com preço detectado
    # 3. total de anúncios < min_ads_ratio (50%) da média histórica recente
```

Se qualquer uma falhar: **nada é gravado**, e um alerta sai pelo Telegram. O
próprio módulo documenta a filosofia (`scraper/sanity.py:1-9`): *"Fail loud, não
fail silent: é melhor perder uma rodada de dados do que encher o banco com
linhas de preço=None por dias até alguém notar o dashboard parado."*

Isso é a rede de segurança citada no README como resposta a "requests pode
parar de funcionar" — se a OLX endurecer proteção a ponto de bloquear
requisições simples, ou mudar de framework, o sintoma aparece primeiro aqui
(Telegram avisando "possivelmente quebrado"), não como um banco silenciosamente
enchendo de lixo.

### 3.7 Limitações do scraping (documentadas, não escondidas)

Do próprio README (`../README.md:90-104`), mais uma observação minha ao vivo:

- **Partida fria dos alertas de oportunidade.** `common/stats.py` só confia na
  mediana de um grupo com pelo menos `oportunidade_amostra_minima` (5) anúncios
  ativos. Em 02/09/2026, quando as 3 categorias ainda estavam ativas, medindo
  direto no banco: monitor tinha 15 de 31 grupos distintos com amostra
  confiável, iPhone 30 de 94, computador 22 de 50 — a maioria dos grupos de
  iPhone e boa parte dos de computador não geravam alerta de oportunidade,
  mesmo com centenas de anúncios ativos no total. Essa mesma dinâmica —
  cauda longa de grupos pequenos demais pra confiar na mediana — se aplica
  hoje à expansão multi-UF do iPhone: cada estado novo começa do zero de
  amostra por grupo.
- **`requests` pode parar de funcionar** se a OLX endurecer a proteção a ponto
  de bloquear requisições simples — o checkpoint de sanidade é a rede de
  segurança, não uma prevenção.
- **A extração de JSON depende do formato atual do Next.js da OLX.** Mudança de
  framework ou de serialização quebra o parser (com erro claro, ver 3.4), exige
  ajuste manual.
- **(Observação ao vivo, não documentada no código)** ver 3.3 acima — os totais
  de 500/categoria/rodada de hoje merecem a mesma investigação que já resolveu
  o teto de 250 antes.

---

## 4. Banco de dados

### 4.1 Tabelas

Todo o schema vive em `common/storage.py:42-125`, uma única string SQL
executada via `conn.executescript()`.

**`anuncios`** — 1 linha por anúncio distinto (não por rodada de coleta).
Colunas de specs de **todas** as categorias convivem na mesma tabela, nullable
(um anúncio de monitor não usa `modelo`/`cpu_modelo`; um de iPhone não usa
`hz_exato`) — ver 4.2 pra por quê disso ser proposital.

**`historico_precos`** — 1 linha só quando o preço de um anúncio **cai** em
relação ao valor salvo. Alta de preço atualiza `anuncios.preco` (reflete a
verdade) mas não gera linha aqui — só queda é sinal de oportunidade
(`common/storage.py:10-13`).

**`coletas`** — 1 linha por rodada de coleta *por categoria*, com contagens
agregadas (`total_anuncios`, `novos`, `quedas_preco`, `com_preco`). É o que
alimenta o checkpoint de sanidade (média histórica) e o que usei pra medir
uptime real na seção 7.

**`vendas`** — registro manual (não preenchido pelo scraper) de compra/revenda.
Comentário no próprio schema (`common/storage.py:110-112`): *"é o que permite
comparar margem estimada com margem real algum dia."* Ainda **0 registros**
(confirmado 02/09/2026 e reconfirmado 15/09/2026) — a aba ainda não foi usada
pra nenhuma compra real.

### 4.2 Relacionamentos

**Não há `FOREIGN KEY` declarada em nenhuma tabela** — conferido direto na
string `_SCHEMA`. As relações são lógicas, não impostas pelo SQLite:

- `historico_precos.listing_id + plataforma` → junta com `anuncios` nas
  consultas (`dashboard/queries.py:27`, `JOIN anuncios a ON a.listing_id =
  h.listing_id AND a.plataforma = h.plataforma`).
- `coletas.categoria` → mesmo domínio de valores que `anuncios.categoria`
  (`monitor`/`iphone`/`computador`), sem constraint que impeça um valor
  diferente.
- `vendas` é solta, sem referência a `anuncios` — é preenchida por texto livre
  pelo usuário (`titulo`, `url` opcional), não linkada por `listing_id`.

**Por que multi-categoria numa tabela só, em vez de uma tabela por categoria** —
isto está documentado explicitamente (`common/storage.py:22-28`): a coluna
`categoria` + a propriedade `grupo` (calculada por cada schema Pydantic — ver
seção 3.2/5) são o que deixam `common/stats.py` e `common/storage.py`
genéricos. Toda comparação de "isso é oportunidade" agrupa por `(categoria,
grupo)` sem o storage/stats precisar conhecer os campos específicos de cada
categoria — `upsert_ads()` lê os campos de categoria via `getattr(a, "campo",
None)`, então funciona pra qualquer schema com os campos comuns.

### 4.3 Chaves

| Tabela | Primary key |
|---|---|
| `anuncios` | `(listing_id, plataforma)` |
| `historico_precos` | `(listing_id, plataforma, registrado_em)` |
| `coletas` | `(coletado_em, plataforma, categoria)` |
| `vendas` | `id` (autoincrement) |

### 4.4 Índices

```sql
CREATE UNIQUE INDEX idx_anuncios_url    ON anuncios (url);
CREATE INDEX        idx_anuncios_grupo  ON anuncios (categoria, grupo);
CREATE INDEX        idx_anuncios_ativo  ON anuncios (ativo);
CREATE INDEX        idx_historico_listing ON historico_precos (listing_id, plataforma);
```

`idx_anuncios_url` é a segunda camada de proteção contra duplicata (além da
PK). `idx_anuncios_grupo` é o índice que sustenta `preco_mediano_grupo()` e
`medianas_todos_grupos()` (`common/stats.py`) — a consulta mais frequente do
sistema, chamada a cada avaliação de oportunidade e a cada refresh de 60s do
dashboard.

### 4.5 Por que SQLite (e não Postgres/MySQL)

**O que o código documenta, com confiança:** o modo **WAL** (Write-Ahead
Logging), ativado em toda conexão (`common/storage.py:140`,
`PRAGMA journal_mode=WAL`), permite **um escritor (scraper) e múltiplos
leitores (dashboard) simultâneos sem erro `database is locked`**
(`common/storage.py:1-5`, também no README). Isso é exatamente o padrão de
acesso real do sistema.

**O que o código *não* documenta — marcado explicitamente como tal:** por que
SQLite especificamente, e não Postgres/MySQL com um servidor separado, **não
está registrado em nenhum comentário, commit ou conversa que eu tenha acesso**.
A inferência mais razoável, vinda do contexto conhecido do projeto (não do
código): o sistema inteiro roda numa única máquina pessoal, via Docker Desktop
— um arquivo único, sem processo de banco separado pra provisionar/manter,
reduz peça móvel pra um projeto solo desse tamanho. Isso é *minha* leitura do
contexto, não uma decisão documentada — trate como tal numa entrevista ("não
tenho registrado um comparativo formal com Postgres; na prática, rodando numa
máquina só, um arquivo único fez sentido pra manter simples").

### 4.6 Como os dados são persistidos

`upsert_ads()` (`common/storage.py:476-637`) é o coração do storage:

1. `snapshot_estado()` lê o estado atual (`listing_id -> (preço, ativo)`) de
   **todos** os anúncios já vistos **nessa categoria** — filtrar por categoria
   aqui não é cosmético: sem isso, rodar a coleta de iPhone marcaria todo
   monitor ativo como "sumido" (testado explicitamente em
   `test_coleta_de_uma_categoria_nao_desativa_a_outra`).
2. Para cada anúncio da rodada: se não estava no snapshot (ou estava inativo) →
   é **novo**; se preço caiu em relação ao snapshot → é **queda** (as duas
   coisas alimentam `ColetaResultado`, usado depois por `diff.py` pra decidir
   o que notificar).
3. `INSERT ... ON CONFLICT DO UPDATE` grava/atualiza `anuncios` de uma vez
   (`executemany`); `historico_precos` só recebe linha em caso de queda (ou
   "avistamento" inicial de um anúncio novo, com `preco_anterior=NULL`).
4. Todo `listing_id` que estava ativo no snapshot mas **não veio nesta rodada**
   é marcado `ativo=0, removido_em=<agora>` — não é apagado, fica disponível
   pra análise de "tempo até sumir do ar".
5. Uma linha agregada é gravada em `coletas`.

Uma decisão de produto explícita, não só limpeza de banco
(`common/storage.py:15-20`): antes da correção de duplicação, cada anúncio
parado no ar por N rodadas virava N amostras idênticas na mediana — inflando
artificialmente a "amostra mínima" (`oportunidade_amostra_minima`) e enviesando
a mediana pro preço de quem está anunciado há mais tempo, o oposto de "preço
justo de mercado agora".

### 4.7 Migrações automáticas

Três funções, todas chamadas dentro de `init_db()` **antes** do
`executescript()` do schema atual, cada uma auto-detectando se precisa rodar
(idempotente) via `PRAGMA table_info`:

| Função | Detecta | Ação |
|---|---|---|
| `_migrar_schema_legado_se_necessario` | Tabela `anuncios` existe mas falta `primeiro_visto_em` (schema antigo: 1 linha por rodada) | Renomeia pra `anuncios_legado_pre_migracao`, recria o schema novo, faz backfill (calcula quedas reais e estado ativo a partir do histórico bruto) |
| `_adiciona_multi_categoria_se_necessario` | Tabela existe mas falta `categoria` | `ALTER TABLE ADD COLUMN` pra `categoria`/`grupo`/`modelo`/`armazenamento_gb`/`cor`/`saude_bateria`; marca tudo existente como `categoria='monitor'` |
| `_adiciona_campos_computador_se_necessario` | Tabela existe mas falta `cpu_modelo` | `ALTER TABLE ADD COLUMN` pra `cpu_marca`/`cpu_modelo`/`ram_gb`/`inclui_monitor` |

**Por quê automático embutido, e não script manual externo** — documentado no
docstring (`common/storage.py:157-165`): *"sem passo manual, sem depender de
alguém lembrar de rodar uma migration antes do deploy."* Cada migração
preserva os dados antigos (renomeia, nunca `DROP`) e é coberta por teste que
verifica idempotência (rodar `init_db()` duas vezes não duplica nada).

Evidência real de que isso rodou em produção, não só em teste: existem 3
diretórios de backup manual em `data/` — `backup_pre_migracao_20260826/`,
`backup_pre_multicategoria_20260826/`, `backup_pre_computador_20260826/` —
feitos antes de cada uma dessas três migrações rodar contra o banco real.

---

## 5. Backend

### 5.1 Organização do código

```
common/
  config.py   -- Settings (pydantic-settings), única fonte de configuração
  schema.py   -- MonitorAd / IphoneAd / ComputadorAd (Pydantic) + extração de specs
  storage.py  -- conexão SQLite, schema, migrações, upsert, snapshots
  stats.py    -- mediana por grupo, avaliação de oportunidade, filtro de sucata

scraper/
  fetcher.py  -- requests.Session, monta URL de paginação
  parser.py   -- extrai o array "ads" do HTML, valida em objetos do schema
  sanity.py   -- checkpoint antes de gravar
  diff.py     -- traduz ColetaResultado (ids) de volta pra objetos do schema
  notifier.py -- POST pro Telegram Bot API
  main.py     -- orquestra tudo + agenda via APScheduler

dashboard/
  app.py      -- páginas/abas Streamlit
  queries.py  -- leitura do banco (pandas)
  charts.py   -- plotly
  vendas.py   -- CRUD manual da tabela vendas
```

Uma característica real de como isso vira imagem Docker: `common/` é copiado
**separadamente** pra dentro de cada uma das duas imagens (`COPY common
./common` em ambos os Dockerfiles) — não é um pacote Python instalado nem
compartilhado via volume. Se `common/` mudar, as duas imagens precisam ser
reconstruídas.

**Um detalhe de import que vale saber antes de alguém perguntar "o que quebra
se você rodar isso fora do Docker":** dentro do container, `scraper/Dockerfile`
faz `COPY scraper .` — isso achata o conteúdo de `scraper/` pra dentro de
`/app`, então `fetcher.py`, `parser.py`, `sanity.py`, `diff.py`, `notifier.py`
ficam **irmãos** de `common/` em `/app`, não dentro de `/app/scraper/`. É por
isso que `scraper/main.py` importa `from fetcher import ...` (sem prefixo
`scraper.`), enquanto os testes (rodados a partir da raiz do repo, com
`PYTHONPATH=.`) importam `from scraper.fetcher import url_pagina` — mesmo
arquivo, dois contextos de execução diferentes, dois estilos de import
diferentes, ambos corretos no seu contexto. Rodar `python scraper/main.py`
direto da raiz do repo, sem ajustar `PYTHONPATH`, quebraria com
`ModuleNotFoundError: No module named 'fetcher'`.

### 5.2 Responsabilidade de cada módulo

| Módulo | Responsabilidade | Não faz |
|---|---|---|
| `fetcher.py` | HTTP puro — busca HTML, monta URL de página | Não sabe o que é um "anúncio" |
| `parser.py` | Extrai JSON bruto do HTML, valida em objetos tipados | Não decide se algo é oportunidade |
| `sanity.py` | Decide se uma rodada é confiável o bastante pra gravar | Não grava nada |
| `storage.py` | Persistência, migração, snapshot de estado | Não decide o que é "novo" pro negócio (`diff.py` faz isso, sobre o resultado do storage) |
| `diff.py` | Traduz ids (`ColetaResultado`) de volta pra objetos completos | Não acessa o banco |
| `stats.py` | Mediana, filtro de confiabilidade, matemática de margem | Não sabe nada de Telegram/HTTP |
| `notifier.py` | Envia texto pro Telegram | Não decide o conteúdo da mensagem |
| `main.py` | Orquestra a ordem acima, formata a mensagem final, agenda | Não reimplementa lógica de nenhuma das camadas acima |

### 5.3 Fluxo de uma "requisição"

Não existe um servidor HTTP recebendo requisição dentro do scraper — o mais
próximo de "requisição" são dois fluxos bem diferentes:

**(a) Uma rodada do scraper** (disparada por tick do `BlockingScheduler`, não
por rede):

```
scheduler tick -> rodar_coleta(nome, search_url, ad_class, categoria)
  -> loop de paginação (fetch_html + extract_ads)
  -> build_ads (valida com Pydantic)
  -> checar_sanidade
  -> upsert_ads (grava, retorna ColetaResultado)
  -> separar_novidades (novos, quedas)
  -> avaliar() por anúncio novo/com queda
  -> enviar_telegram() se eh_oportunidade
```

**(b) Um carregamento de página do dashboard** (esse sim é uma requisição HTTP
de verdade, do browser pro processo Streamlit):

```
GET / (ou refresh do st.fragment a cada 60s)
  -> Streamlit reexecuta app.py de cima a baixo (modelo de execução do Streamlit)
  -> st.radio decide `categoria` (filtro ativo)
  -> secao_kpis / secao_alertas (fragments com refresh próprio de 60s,
     independentes do resto da página)
      -> queries.carregar_ativos (pd.read_sql_query)
      -> stats.medianas_todos_grupos + avaliar_preco (Python puro, sem nova
         consulta por linha — ver o bug de N+1 query corrigido, seção 9.5)
  -> st.dataframe / st.metric / plotly renderizam o resultado
```

---

## 6. API

**Correção de enquadramento, antes de qualquer outra coisa:** este projeto
**não expõe uma API própria**. Não há endpoint HTTP que receba requisição e
devolva JSON — nem o scraper (roda em ciclo, sem porta), nem o dashboard
(serve páginas Streamlit renderizadas, não uma API que outro programa chamaria
programaticamente). Se uma entrevista perguntar "quais APIs o projeto expõe", a
resposta correta é "nenhuma — ele *consome* duas".

### 6.1 A "superfície" de dados da OLX

Não é uma API oficial nem documentada publicamente — é o HTML público das
páginas de busca, cujo payload de streaming do Next.js embute os dados de cada
anúncio como JSON (ver seção 3.2).

- **Entrada:** URL de busca (`olx_search_url`/`iphone_search_url`/
  `computador_search_url` em `common/config.py`) + número de página.
- **Processamento:** `fetch_html` → `extract_ads` (extração por profundidade de
  colchete + desescape via `json.loads`) → `build_ads` (validação Pydantic).
- **Saída:** lista de objetos `MonitorAd`/`IphoneAd`/`ComputadorAd`, tipados e
  validados — ou uma lista vazia/erro claro se o formato mudou.

### 6.2 Telegram Bot API

Essa sim é uma API oficial e documentada (`https://api.telegram.org`).
`scraper/notifier.py` usa só o endpoint `sendMessage`, via `requests.post`
puro — **não** usa a biblioteca `python-telegram-bot`.

**Por quê `requests` puro, não a lib oficial** — documentado
(`scraper/notifier.py:1-9`): o projeto só precisa **enviar** mensagem, nunca
receber ou lidar com updates (comandos, callbacks) — trazer um framework de bot
inteiro pra isso seria peso morto na imagem Docker. Uma chamada POST resolve.

- **Entrada:** mensagem de texto já formatada (título, preço, condição, margem
  estimada, link).
- **Processamento:** `POST /bot<token>/sendMessage`, `timeout=10`, **sem**
  `parse_mode` (texto puro, sem Markdown/HTML).
- **Saída:** notificação assíncrona no chat configurado — ou, se falhar
  (rede, credenciais ausentes), só um log de aviso/erro, **nunca** uma exceção
  que derrube o scraper (`notifier.py:45-47`).

**Por que sem `parse_mode`** — este teve um bug real por trás
(`scraper/notifier.py:11-17`, commit `955103d`): a mensagem embute texto que o
sistema não controla (título de anúncio escrito pelo vendedor, texto de
exceção). Um `_`/`*` desbalanceado nesse texto fazia o Telegram rejeitar com
`400 "can't parse entities"` — derrubando em silêncio justo o alerta que devia
ser a rede de segurança. Achado ao vivo: um erro de banco com
`historico_precos.preco` no texto (os `_` do nome da coluna) bastou pra
disparar isso.

---

## 7. Docker

### 7.1 Por que containerizar

Não há um comentário explícito do tipo "escolhemos Docker porque X" comparando
com a alternativa de não containerizar. O que **está** documentado, com
evidência concreta, é um benefício específico e verificável: **a imagem sempre
usa Python 3.11 (`python:3.11-slim`), independente da versão instalada na
máquina host** — e isso importa de verdade aqui.

Eu confirmei isso na prática, não só lendo o README: tentei instalar as
dependências deste projeto localmente, fora do Docker, num ambiente com Python
3.14 (a única versão instalada nesta máquina hoje). O resultado reproduziu
exatamente o que o README descreve (`../README.md:42-47`):

```
pandas: "Could not find ...vswhere.exe" (tenta compilar via Meson, sem toolchain C)
pydantic-core: "link.exe failed" (tenta compilar via Rust/maturin, sem toolchain configurado)
```

Ou seja: `pydantic==2.9.2` e `pandas==2.2.3` (as versões travadas nos
`requirements.txt`) não têm wheel pré-compilada pra Python 3.14, e essa máquina
não tem as ferramentas de build (Visual Studio C++ / Rust) pra compilar do
zero. **Isso nunca acontece dentro do Docker**, porque a imagem sempre parte de
`python:3.11-slim`, uma versão pra qual essas wheels existem prontas — pin de
versão elimina a variável "qual Python está instalado na máquina de quem vai
rodar isso" por completo.

### 7.2 O que acontece no Dockerfile

Os dois Dockerfiles (`scraper/Dockerfile`, `dashboard/Dockerfile`) seguem o
mesmo formato, single-stage:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY <serviço>/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # <- antes de copiar o código
COPY common ./common
COPY <serviço> .
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
CMD [...]
```

Instalar dependências **antes** de copiar o código-fonte (em vez de copiar tudo
de uma vez) aproveita o cache de camadas do Docker — mudar um `.py` não força
reinstalar `requirements.txt` no rebuild seguinte. Isso é uma prática padrão de
Dockerfile; não há comentário confirmando que foi deliberado aqui, mas o
resultado é esse.

Diferença entre os dois: `dashboard/Dockerfile` expõe a porta `8501`
(`EXPOSE 8501`) e roda `streamlit run app.py --server.address=0.0.0.0` (sem
isso, o Streamlit por padrão só escuta em `localhost`, inacessível de fora do
container); `scraper/Dockerfile` não expõe porta nenhuma — roda
`python main.py`, um processo que nunca aceita conexão de rede.

### 7.3 Dependências

Cada serviço tem seu próprio `requirements.txt`, sem um arquivo compartilhado
na raiz:

- `scraper/requirements.txt`: `requests`, `pydantic`, `pydantic-settings`,
  `apscheduler` — nada de `pandas`/`plotly`/`streamlit` (o scraper não
  precisa).
- `dashboard/requirements.txt`: `streamlit`, `plotly`, `pandas`, `pydantic`,
  `pydantic-settings` — repete `pydantic`/`pydantic-settings` porque
  `dashboard/app.py` importa `common.config`/`common.stats`, que dependem
  deles.

### 7.4 Ambiente reproduzível

`WORKDIR /app` fixa o diretório de trabalho; `PYTHONPATH=/app` é o que faz
`from common.config import settings` resolver dentro do container (ver seção
5.1 pro porquê disso interagir com o `COPY <serviço> .` achatado);
`PYTHONUNBUFFERED=1` garante que `print`/log apareçam em tempo real no
`docker logs`, sem ficar preso em buffer — importante pra um processo de longa
duração que você quer conseguir debugar olhando o log ao vivo.

### 7.5 Limitações (Docker e infraestrutura)

- **Sem healthcheck** em nenhum dos dois serviços no `docker-compose.yml` —
  `depends_on: scraper` no dashboard controla só **ordem de start**, não
  prontidão (o dashboard pode subir e tentar ler o banco antes do scraper
  criar o arquivo, embora `init_db()` seja idempotente e crie o schema se
  faltar).
- **Sem CI configurado** — não existe diretório `.github/` neste repositório;
  os 97 testes (seção 9) só rodam quando alguém executa `pytest` manualmente.
- **`restart: unless-stopped` não ajuda quando é o *host* que dorme, não o
  container.** Esta é a limitação mais concreta e mensurável do projeto hoje,
  então vale o espaço:

  O scraper roda via Docker Desktop numa máquina Windows pessoal — não um
  servidor sempre ligado. Cruzando os timestamps reais da tabela `coletas`
  (consulta feita hoje, 02/09/2026, direto no banco de produção):

  | Categoria | Rodadas reais | Rodadas esperadas (12 min, janela corrida) | Uptime real | Maior buraco entre rodadas |
  |---|---|---|---|---|
  | monitor | 334 | ~1.272 (10,6 dias) | **~26%** | 65,9h |
  | iPhone | 175 | ~904 (7,5 dias) | **~19%** | 65,9h |
  | computador | 167 | ~898 (7,5 dias) | **~19%** | 65,7h |

  **Nota da revisão de 15/09/2026:** esta tabela é uma medição histórica de
  02/09/2026, de quando as 3 categorias ainda rodavam em paralelo — monitor e
  computador pararam de ser agendados em 12/09/2026 (seção 1), então essas
  duas linhas não têm mais rodada nova sendo produzida. A causa raiz (host
  pessoal que hiberna) segue idêntica pra iPhone, hoje rodando em múltiplas
  UFs na mesma máquina.

  **Remedição de iPhone, feita hoje (15/09/2026), mesma consulta na tabela
  `coletas`, últimos 7 dias corridos:** 174 rodadas reais de 840 esperadas
  (a cada 12 min) — **uptime ~21%**, maior buraco de **4 dias e 13,7h**
  seguidos sem nenhuma rodada (08–13/09/2026). Uptime não melhorou com o
  pivô pra iPhone (o gargalo sempre foi a máquina hibernar, não a
  categoria); o número bate no mesmo patamar de antes. Esse buraco de 4,5
  dias específico é grande demais pra ser só o bug de duplicata-na-mesma-
  rodada da seção 3.5/9.5#5 (que perde 1 rodada isolada por vez, não vários
  dias seguidos) — é consistente com a máquina real ficar desligada por um
  período longo. O dashboard agora expõe esse número ao vivo (KPI "Última
  coleta" + uptime, seção 8.2), em vez de só existir nesta tabela estática.

  Os dois maiores buracos (quase 66h cada) acontecem **no mesmo horário nas 3
  categorias simultaneamente** — não é falha de uma categoria específica, é a
  máquina inteira pausando (notebook hibernando/desligado). O `docker ps`
  mostra o container como "Up" o tempo todo durante esses buracos — o relógio
  do container não reflete o host suspenso, então "container rodando" não é
  garantia nenhuma de coleta acontecendo. Isso já tinha sido identificado numa
  auditoria anterior (25–26/08/2026, quando o uptime medido era ~48%) — **o
  quadro piorou desde então**, não melhorou.
  
  Uma alternativa (hospedar num servidor sempre ligado, tipo VPS) foi
  considerada e **deliberadamente adiada**, não por desconhecimento — ver seção
  10, linha "Onde hospedar o scraper", pro porquê.

---

## 8. Streamlit

### 8.1 Como a interface conversa com os dados

Sem camada de API entre dashboard e banco: `dashboard/queries.py` chama
`pd.read_sql_query()` direto contra o mesmo arquivo SQLite que o scraper
escreve (`common/storage.get_connection()`, compartilhado entre os dois
serviços via o volume Docker `./data:/data`). O modo WAL é o que torna isso
seguro (seção 4.5).

### 8.2 Estado

- **`@st.fragment(run_every=60)`** em `secao_kpis` e `secao_alertas`
  (`dashboard/app.py:61-62,91-92`) — essas duas seções se atualizam sozinhas a
  cada 60s, **sem** re-renderizar a página inteira (diferente de um
  `st.rerun()` manual ou de um refresh de browser). O resto da página
  (tendência, mercado, vendas, sobre) só atualiza quando o usuário navega entre
  abas.
- **Filtro de categoria como estado implícito:** `st.radio` no topo
  (`dashboard/app.py:398-406`) decide `categoria`, passado como parâmetro pras
  funções de seção — não há `st.session_state` explícito guardando isso; o
  modelo de execução do Streamlit (reexecuta o script inteiro a cada interação)
  já resolve.
- **Formulários (`aba Vendas`):** `st.form(..., clear_on_submit=True)` +
  `st.rerun()` depois de gravar (`dashboard/app.py:330-339`) — limpa o
  formulário e força reler o banco imediatamente, sem esperar o próximo ciclo
  de 60s do fragment.
- **Adicionado em 15/09/2026 — frescor da coleta como KPI:** `secao_kpis`
  agora tem uma 5ª coluna, "Última coleta" (`_frescor_da_coleta()`,
  `dashboard/app.py:43-58`), calculada a partir de `carregar_coletas()` —
  há quantos minutos foi a última rodada, mais uptime dos últimos 7 dias
  quando uma categoria está selecionada (uptime não é calculado pra "Todas":
  cada categoria tem sua própria agenda, não faz sentido uma frequência
  esperada única). Primeira vez que "o dado tá atualizado?" vira número
  visível no dashboard, em vez de só existir em `coletas`/nesta seção 7.5.

### 8.3 Filtros

`st.radio("Categoria", ["iPhone", "Monitor", "Computador", "Todas"])` continua
cobrindo as 3 categorias (`dashboard/app.py:398`) — inclusive as
descontinuadas, porque o dashboard explora dado histórico, não só o que está
sendo coletado agora. `categoria=None` (opção "Todas") remove o filtro `WHERE
categoria = ?` nas queries (`dashboard/queries.py:12-18`) e nas medianas
(`common/stats.py:138-161`) — mesma função, tratando ausência de filtro como
caso explícito, não um valor mágico.

Com a categoria "iPhone" selecionada, aparece um segundo seletor,
`st.radio("Estado", ["Todos"] + ufs_iphone)` (`dashboard/app.py:421`),
populado a partir de `settings.iphone_regioes` — reflexo direto da expansão
multi-UF (seção 2.1).

**Ordem do radio trocada em 15/09/2026** (era `["Monitor", "iPhone",
"Computador", "Todas"]`, "Monitor" abria por padrão por ser o primeiro item):
achado por inspeção visual que a categoria padrão do dashboard era uma
categoria morta — monitor parou de ser coletado em 12/09/2026, então seus
anúncios ficam com `ativo=1` congelado (nunca mais re-checados), e a
"oportunidade" calculada contra a mediana desse grupo (também congelada)
produzia margens de 300-400% que pareciam outlier de cálculo mas eram, na
prática, ausência de atualização — ver seção 9.5#6. Corrigido: iPhone (a
categoria ativa) é o padrão agora, e um `st.warning()` (`dashboard/app.py:407-414`)
aparece sempre que Monitor, Computador ou Todas é selecionado, explicando
que aquele dado está congelado.

### 8.4 Visualização

| Elemento | Onde | O que mostra |
|---|---|---|
| KPIs (`st.metric` x5) | `secao_kpis` | Anúncios ativos, oportunidades agora, margem mediana das oportunidades, quedas de preço (7 dias), última coleta (+ caption de uptime) |
| Tabela de oportunidades | `secao_alertas` | Mesmo critério do Telegram, ordenada por margem % — `st.dataframe` com `column_config` formatando moeda e link clicável |
| Histograma de distribuição de preço | `charts.grafico_distribuicao_preco` (plotly) | Adicionado em 15/09/2026 — mediana e média marcadas com `add_vline`, explica visualmente por que `common/stats.py` usa mediana (robusta a outlier); primeira coisa em `secao_mercado`, antes dos gráficos que já existiam |
| Tempo médio no ar | `secao_mercado` (`st.metric` x2) | Adicionado em 15/09/2026 — mediana de dias entre `primeiro_visto_em` e `removido_em` (`queries.carregar_tempo_no_ar`), métrica de ciclo de vida/giro de mercado |
| Scatter Preço × Hz | `charts.grafico_dispersao_hz` (plotly) | Só pra monitor (precisa de `hz_exato`/`tipo_monitor`) — cor por desvio da mediana do subconjunto |
| Scatter de tendência de queda | `charts.grafico_tendencia_quedas` (plotly) | Quedas reais ao longo do tempo, tamanho do marcador = % de queda — só existe por causa do schema novo (1 linha por queda real, não por rodada) |
| `st.bar_chart` x2 | `secao_mercado` | Distribuição por município, marcas/modelos mais frequentes |
| Formulários + tabela de histórico | `secao_vendas` | Registro manual de compra/venda, margem real vs. estimada |

---

## 9. Qualidade

### 9.1 Tratamento de exceções

Já detalhado por camada na seção 3.4 — o padrão geral é: **cada etapa do
pipeline tem seu próprio tipo de erro esperado, capturado no nível mais baixo
possível, sempre resultando em "avisa e desiste da rodada" em vez de deixar
uma exceção não tratada subir e matar o processo inteiro do scraper.** Isso
valia originalmente pras 3 categorias rodando no mesmo `BlockingScheduler` (um
erro não capturado numa mataria as outras duas); hoje o mesmo isolamento vale
por região de iPhone dentro de `rodar_coleta_iphone_todas_regioes` — uma
região falhando não derruba as demais nem o job diário de resumo.

### 9.2 Logs

`logging` da stdlib, sem framework externo. `scraper/main.py:32-33` configura
`basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s
%(message)s")` uma vez, no entrypoint. Módulos individuais (`storage.py`,
`parser.py`, `notifier.py`) pegam seu próprio logger via
`logging.getLogger(__name__)` — migrações de schema logam em `WARNING`
deliberadamente (visível mesmo que alguém suba o nível mínimo de log).

### 9.3 Validação

Todo dado bruto da OLX passa por um `pydantic.BaseModel` (`MonitorAd`/
`IphoneAd`/`ComputadorAd`) antes de tocar o banco. Dois detalhes de design
deliberados:

- **`Optional[str]` em vez de `Literal[...]`** pros campos de enumeração da OLX
  (condição, tipo de tela, etc.) — comentário explícito no código
  (`common/schema.py:148-149`): *"um valor inesperado da OLX não pode derrubar
  a validação do anúncio inteiro."* Um `Literal` rejeitaria qualquer valor novo
  que a OLX inventasse; `Optional[str]` aceita e segue em frente.
- **Preço ausente vira `None`, nunca `0`** (`_limpa_preco_valor`,
  `common/schema.py:119-128`) — um `0` pareceria uma pechincha impossível e
  contaminaria a mediana da camada de oportunidade.

### 9.4 Testes

**97 testes em 8 arquivos, 1.612 linhas de teste contra 2.119 linhas de código
de aplicação** (`common/` + `scraper/` + `dashboard/`, sem contar os próprios
testes) — quase 0,76 linha de teste por linha de código.

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_schema.py` | 33 | Extração de specs das 3 categorias, recuperação de marca/CPU/RAM do título |
| `test_stats.py` | 26 | Mediana por grupo, filtro de sucata/preço implausível, avaliação de oportunidade, teto de orçamento |
| `test_storage.py` | 20 | Upsert, deduplicação, migrações (3 cenários + idempotência), isolamento entre categorias |
| `test_parser.py` | 5 | Extração do JSON embutido, colchetes aninhados, item malformado |
| `test_paginacao.py` | 4 | Loop de paginação, condição de parada |
| `test_sanity.py` | 4 | Checkpoint de sanidade (zero anúncios, preço faltando, queda abrupta) |
| `test_charts.py` | 4 | Gráfico de tendência, incluindo o caso `preco_anterior=0` (ver 9.5) |
| `test_integration_amostra_real.py` | 1 | End-to-end com um payload modelado a partir de dado real da OLX |

**Um padrão que se repete no arquivo inteiro de testes:** boa parte deles é
literalmente uma prova de regressão de um bug real, encontrado testando contra
dado de produção — não só casos hipotéticos escritos antes do código existir.
Exemplos citados pelo nome do teste: `test_margem_e_confiavel_pelo_titulo_
quando_condicao_esta_errada`, `test_avaliar_preco_abaixo_do_piso_nao_e_
oportunidade`, `test_grafico_tendencia_quedas_ignora_preco_anterior_zero_sem_
quebrar` — cada um nasceu de um bug visto ao vivo no dashboard ou no banco real,
não de uma lista de casos de teste pensada em abstrato.

**Verificação que eu fiz, não só assumi:** tentei rodar a suíte de testes nesta
máquina (Python 3.14, sem Docker) e reproduzi exatamente a falha que o README
prevê — `ModuleNotFoundError: No module named 'pydantic'` sem as dependências
instaladas, e, tentando instalar num venv isolado, os mesmos erros de
compilação da seção 7.1 (`pandas`/`pydantic-core` sem wheel pra 3.14, sem
toolchain de build). Isso não é uma falha do projeto — é exatamente a limitação
que o README já documenta e assume, e ela se comporta como documentado.

### 9.5 Bugs reais corrigidos, como evidência de processo (não só de sorte)

Vale registrar 6 exemplos completos — cada um mostra o mesmo padrão: achado
testando contra dado real (não só em teste automatizado escrito a priori),
diagnosticado, corrigido, e (em todos exceto o N+1 e o de UX do dashboard)
coberto por teste de regressão depois.

1. **Divisão por zero no gráfico de tendência** (commit `8ea886d`): um anúncio
   "doação" (R$0) reaparecendo gerava `preco=0` **e** `preco_anterior=0` na
   mesma linha de histórico → `queda_pct = 0/0 = NaN` → Plotly quebrava a
   página inteira tentando usar `NaN` como tamanho de marcador. Corrigido
   filtrando `preco_anterior > 0` antes de calcular a porcentagem
   (`dashboard/charts.py:39`).
2. **Perda silenciosa de rodada inteira** (commit `955103d`): um anúncio novo
   sem preço detectado (`preco=None`) fazia o `INSERT` em `historico_precos`
   violar o `NOT NULL` daquela coluna — `upsert_ads()` estourava exceção, a
   rodada inteira da categoria era descartada sem gravar nada. Corrigido não
   tentando gravar histórico quando não há preço pra registrar
   (`common/storage.py:538-543`).
3. **Grupo genérico corrompendo a mediana de computador**: 19 de 250 anúncios
   sem `cpu_modelo` caíam todos no grupo `"CPU (?)"`, misturando um PC de
   R$450 com um de R$2000+ na mesma mediana — mesma família do bug de marca
   "Outros" que já tinha sido corrigido pra monitor. Corrigido reaplicando a
   mesma técnica (recuperar do título via regex).
4. **N+1 query no painel de alertas** (achado e corrigido antes do primeiro
   commit sob controle de versão, registrado na auditoria de 25–26/08/2026):
   o painel de oportunidades abria **1 conexão SQLite nova por anúncio, a cada
   60 segundos de refresh** — até ~290 conexões por ciclo, chamada de dentro de
   um `df.apply`. Corrigido trazendo a mediana de **todos** os grupos numa
   única consulta (`common/stats.py:125-154`,
   `medianas_todos_grupos()`), com o resto sendo aritmética em Python sobre o
   DataFrame já carregado — é o que o fluxo de requisição da seção 5.3(b)
   descreve hoje.
5. **Duplicata dentro da mesma rodada quebrando `historico_precos`** (achado
   e corrigido ao vivo em 15/09/2026, mesma família do bug #2 acima — exceção
   no upsert descarta a rodada inteira): a paginação da OLX repetiu 3 de 500
   anúncios de iPhone/ES numa única coleta, gerando duas tentativas de
   `INSERT` com a mesma chave primária em `historico_precos`
   (`listing_id, plataforma, registrado_em` — o mesmo `momento` é
   compartilhado por toda a rodada). `UNIQUE constraint failed`, rollback
   implícito, rodada inteira perdida sem aviso além de um alerta genérico no
   Telegram. Corrigido deduplicando `ads` por `listing_id` no início de
   `upsert_ads()` (`common/storage.py`). Ver seção 3.5 pro detalhe completo
   e a hipótese de que isso já vinha contribuindo pros buracos de uptime da
   seção 7.5. Teste:
   `tests/test_storage.py::test_listing_duplicado_na_mesma_rodada_nao_quebra_a_rodada_inteira`.
6. **Categoria descontinuada como padrão do dashboard, produzindo margem
   fantasma** (achado por inspeção visual em 15/09/2026, não por exceção):
   o seletor de categoria (`dashboard/app.py`) abria em "Monitor" por
   padrão — uma categoria que parou de ser coletada em 12/09/2026. Como
   `ativo=1` só é corrigido por uma rodada de coleta que não roda mais pra
   essa categoria, os anúncios de monitor ficam congelados como "ativos"
   indefinidamente, e a "oportunidade" calculada contra a mediana desse
   grupo (também congelada) chegava a 300-400% de margem — não é erro de
   cálculo, é ausência de atualização mascarada de outlier. Corrigido
   trocando o padrão pra iPhone e adicionando um `st.warning()` explícito
   quando Monitor/Computador/Todas é selecionado.

### 9.6 O que não existe

Pra ser preciso sobre limitações de qualidade, não só sobre pontos fortes: não
há linter nem formatter configurado (nenhum `.flake8`, `pyproject.toml` com
`[tool.ruff]`/`[tool.black]`, ou pre-commit hook no repositório) — o estilo
consistente do código é resultado de disciplina manual, não de uma ferramenta
automatizada. Não há type-checking estático configurado (`mypy`/`pyright`)
apesar do uso extensivo de type hints.

---

## 10. Decisões técnicas

Metodologia desta tabela: a coluna **Por quê** só contém razão com fonte
verificável (código, commit, teste, ou conversa registrada — indicado entre
parênteses). Quando a razão de uma escolha não está registrada em nenhuma
fonte, a célula diz isso explicitamente em vez de apresentar uma suposição como
fato. A coluna **Trade-off** é análise técnica minha sobre a consequência da
escolha — não uma alegação histórica.

| # | Problema | Alternativas | Escolha | Por quê | Trade-off |
|---|---|---|---|---|---|
| 1 | Acessar/ler anúncios da OLX | `requests` puro / browser automatizado (Playwright) | `requests` puro | A OLX renderiza no servidor e embute os dados como JSON no HTML — não precisa executar JS pra ler (README + `fetcher.py`) | Depende do formato atual de serialização do Next.js da OLX; se mudar, parser quebra (com erro claro, não silencioso) |
| 2 | Banco de dados | SQLite / Postgres-MySQL (servidor separado) | SQLite, modo WAL | WAL permite 1 escritor + N leitores simultâneos sem lock — exatamente o padrão de acesso real (`storage.py`). Por que SQLite *especificamente* (vs. Postgres): **não determinado pelo código** | Arquivo único não escala pra múltiplos hosts escrevendo ao mesmo tempo; concorrência real limitada a 1 processo escritor |
| 3 | Persistência de anúncio visto em várias rodadas | 1 linha por rodada / 1 linha por anúncio (upsert) | Upsert | Schema antigo gerava 98,6% de redundância (20.250 linhas → 292 anúncios) e distorcia a mediana de oportunidade (achado real, auditoria 25–26/08) | Exige migração automática — 3 funções de migração acumuladas em `storage.py`, uma por mudança de schema, crescendo a cada categoria nova |
| 4 | "Preço justo" de um grupo | Mediana / média | Mediana | Mais robusta a um anúncio de brincadeira por R$1 ou outlier de loja profissional inflando o grupo (docstring `stats.py`) | Não pondera por volume/recência — um grupo com 5 anúncios pesa a mesma fórmula que um com 80 |
| 5 | Comparar preço entre anúncios diferentes | Um critério único (ex.: só marca) / critério específico por categoria | `grupo` calculado por schema (marca+tipo / modelo+armazenamento / CPU+RAM) | Marca sozinha não discrimina iPhone (sempre "Apple") nem computador (quase metade "Outros" — monta avulsa); o que separa preço é config, não fabricante (`schema.py`) | `grupo` de computador não inclui GPU, que pesa muito no preço — margem alta em computador é sinal pra conferir manualmente, não número final confiável (auditoria 25–26/08) |
| 6 | Decidir se um anúncio vira alerta | Só limiar percentual (% da mediana) / limiar + piso + teto | 3 camadas: `oportunidade_limiar` + `orcamento_minimo_*` + `orcamento_maximo_*` | Margem % boa não basta: preço implausível (ex. iPhone 11 por R$10, visto ao vivo 27/08) nem preço fora do orçamento real do usuário deveriam alertar, mesmo com % ótima (commits `b1c6e6f`, `c6da790`) | Tetos fixos por categoria (não por modelo específico) — um item topo de linha caríssimo nunca alerta, mesmo com desconto genuíno, por design |
| 7 | Excluir anúncio de "sucata" da mediana | Só campo estruturado (`condicao`) / campo + regex de título | Dois sinais, ambos precisam concordar (`margem_e_confiavel`) | Achado ao vivo: vendedor descreve defeito no título ("COM DEFEITO NÃO LIGA") mas marca "Usado - Excelente" no formulário — um sinal só deixava passar (`stats.py`) | Regex é rede de segurança probabilística, documentada como tal no código — nunca cobre 100% das frases possíveis de defeito em português |
| 8 | Aplicar mudança de schema em banco já em produção | Script de migration manual / auto-detecção embutida em `init_db()` | Automática e idempotente | "Sem passo manual, sem depender de alguém lembrar de rodar uma migration antes do deploy" (docstring `storage.py`) | Lógica de migração acumula dentro de `storage.py` — 3 funções hoje, cresce a cada schema novo, sem um framework de migration versionada |
| 9 | Notificar por Telegram | Lib `python-telegram-bot` / `requests` puro contra `sendMessage` | `requests` puro | Só precisa enviar, nunca receber updates — biblioteca inteira seria peso morto na imagem Docker (docstring `notifier.py`) | Sem retry/rate-limit automático da lib — tratado manualmente com try/except simples |
| 10 | Formatar mensagem do Telegram | Markdown (`parse_mode`) / texto puro | Texto puro | Bug real: texto não controlado (título de anúncio, exceção) com `_`/`*` desbalanceado quebrava o envio com erro 400, derrubando o próprio alerta de segurança (commit `955103d`) | Mensagem sem negrito/formatação — só texto corrido |
| 11 | Intensidade da paginação (`max_paginas`) | Deixar em 5 / moderado (10) / generoso (20+) | 10 (moderado) | Teto de 5 cortava a coleta antes da parada natural, mascarando parte do mercado (commit `c553b83`); usuário escolheu explicitamente "moderado" sobre "generoso" quando perguntado (27/08/2026), alinhado com a filosofia de scraper discreto do README | Observação ao vivo (02/09/2026, não documentada): totais batendo exatamente 500/categoria/rodada de novo — mesmo padrão que motivou subir de 5→10 antes pode estar se repetindo |
| 12 | Onde hospedar o scraper | PC pessoal (Docker Desktop) / VPS | PC pessoal, com Tailscale registrado como caminho de acesso remoto (não implementado como VPS) | IP de datacenter (Hetzner/Vultr/etc.) tem taxa de bloqueio maior que IP residencial em site com proteção anti-bot ativa como a OLX — risco identificado e decisão registrada explicitamente (auditoria 25–26/08) | Uptime medido em 02/09/2026 em ~19–26% do esperado, com buracos de até 65,9h (número histórico, ver nota na seção 7.5) — a máquina dormir custa janela de oportunidade real, silenciosamente (Docker mostra "Up" mesmo com host suspenso) |
| 13 | Continuar 3 categorias em paralelo indefinidamente, ou concentrar esforço na que performa | Manter monitor + iPhone + computador / descontinuar as fracas e escalar a forte geograficamente | Descontinuar monitor e computador; escalar iPhone pra múltiplas UFs (`settings.iphone_regioes`) | Código registra o motivo direto: "mercado se mostrou ineficaz" pra monitor/computador (commit `80e4585`, 12/09/2026) — decisão tomada sobre semanas de dado real coletado em paralelo, não intuição (ver seção 1) | Perde a diversificação de categoria (parser modular de `schema.py` ficaria ocioso pras duas); ganha amostra por grupo mais rápida numa única categoria, e testa a hipótese de escala geográfica em vez de escala por produto |

---

## Fontes deste documento

- **Código-fonte completo**, lido integralmente: todos os `.py` em `common/`,
  `scraper/`, `dashboard/`, `tests/`; `docker-compose.yml`; os dois
  `Dockerfile`; os dois `requirements.txt`; `.env.example`; `.gitignore`;
  `README.md`.
- **Histórico completo do Git**: 13 commits, `20689f4` (26/08/2026) até
  `fe8f1bf` (27/08/2026), mensagens completas lidas via `git log`.
- **Suíte de testes**: 97 testes lidos integralmente (não só executados).
- **Auditoria "Raio-X do Monitor Gamer"**, artifact de 25–26/08/2026 (linkado
  em `dashboard/app.py:323`, aba "Sobre o negócio") — origem documentada das
  faixas de orçamento por categoria e da análise de distribuição de preço.
- **Consulta ao vivo ao banco de produção** (`data/olx_monitor.db`), em
  02/09/2026: contagens por categoria, cobertura de amostra por grupo,
  cadência real de coleta (tabela `coletas`).
- **Verificação prática nesta máquina**: tentativa real de instalar
  dependências e rodar os testes fora do Docker (Python 3.14), reproduzindo o
  problema que o README documenta.
- **Memória de conversas anteriores** deste projeto (datadas): fase atual de
  coleta ampla de dados, preferência por scraping discreto, uptime real da
  infraestrutura, origem do relatório Raio-X.
- **Revisão de 15/09/2026 (parte 1)**, reconciliando o documento original
  (02/09/2026) com o estado atual do código e do banco: leitura de
  `common/config.py`, `scraper/main.py` e `dashboard/app.py` como estão
  hoje; `git show 80e4585 -- common/config.py` (mensagem e diff do commit
  que descontinuou monitor/computador); contagens do banco de produção
  naquele momento: 3.967 anúncios únicos (iPhone 2.354, computador 809,
  monitor 804), 5.530 linhas em `historico_precos`, 1.508 linhas em
  `coletas`, 319 linhas em `medianas_diarias` cobrindo 5 dias distintos com
  snapshot.
- **Revisão de 15/09/2026 (parte 2)**, mais tarde no mesmo dia, depois de
  rodar o sistema ao vivo (Docker Desktop religado, `docker-compose up`) pra
  validar dado no dashboard: achado e corrigido o bug de duplicata dentro da
  mesma rodada (seção 3.5/9.5#5) e o bug de categoria padrão do dashboard
  (seção 8.3/9.5#6), os dois reproduzidos contra a OLX/banco reais, não só
  inferidos; suíte de testes crescida de 97 pra 138 (novos testes de
  regressão em `tests/test_storage.py` e `tests/test_charts.py`);
  remedição de uptime de iPhone nos últimos 7 dias direto em `coletas`
  (seção 7.5); contagens atualizadas do banco após a coleta retomar: 4.322
  anúncios únicos (iPhone 2.709, computador 809, monitor 804), 5.909 linhas
  em `historico_precos`, 1.513 em `coletas`. Documentação reorganizada em
  `docs/` (este arquivo incluso) nesta mesma revisão.

Onde nenhuma dessas fontes tinha resposta, este documento diz isso
explicitamente ("não determinado pelo código") em vez de inventar uma
justificativa.

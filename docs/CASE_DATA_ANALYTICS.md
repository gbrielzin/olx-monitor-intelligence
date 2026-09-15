# OLX Monitor Intelligence — Case de Data Analytics

> Este documento existe pra quem está avaliando este projeto sob a ótica de
> **análise de dados** (coleta, tratamento, qualidade, indicadores, decisão),
> não de engenharia de software. Pra profundidade técnica de backend/infra,
> ver [`OLX_DEEP_DIVE.md`](OLX_DEEP_DIVE.md); pra um roteiro de apresentação
> oral (PT/EN), ver [`OLX_PRESENTATION.md`](OLX_PRESENTATION.md).

## Problema

Em qualquer marketplace de itens usados (OLX, no caso), o mesmo produto é
anunciado por preços muito diferentes — e boa parte da diferença não é
qualidade, é só o vendedor não saber o preço justo de mercado, ou precisar
vender rápido. Encontrar essas oportunidades manualmente significa abrir a
busca várias vezes por dia e comparar de cabeça dezenas de anúncios — não
escala e é fácil perder o anúncio bom pra quem viu primeiro.

O projeto nasceu perguntando: **dá pra transformar isso num problema de
dado** — coletar sistematicamente, calcular um preço de referência
estatisticamente confiável e avisar automaticamente quando um anúncio foge
muito pra baixo dele?

## Solução

Um pipeline que roda sozinho 24/7 (dentro do possível — ver limitações de
infra mais abaixo), sem intervenção manual:

**Coleta → tratamento → armazenamento → análise estatística → indicador →
alerta**, com um dashboard pra explorar o resultado e um histórico que
sustenta perguntas de tendência ("esse preço é bom comparado aos últimos 30
dias?"), não só de estado atual.

O projeto **testou 3 categorias** (monitores, iPhones, computadores
completos) e, **com base no dado coletado**, descontinuou duas delas —
detalhe na seção de resultados abaixo.

## Tecnologias

| Camada | Ferramenta | Uso real no projeto |
|---|---|---|
| Linguagem | Python 3.11 | scraper, análise estatística, dashboard |
| Banco de dados | SQLite (modo WAL) | 6 tabelas normalizadas, 1 escritor + 1 leitor simultâneos |
| SQL | consultas via `sqlite3`/`pandas.read_sql_query` | agregações, joins (`historico_precos` × `anuncios`), filtros por categoria/UF/data |
| Validação/qualidade de dado | Pydantic | tipagem e regra de negócio por categoria antes de qualquer linha ir pro banco |
| Análise/estatística | `statistics` (stdlib) | mediana robusta por grupo, tendência temporal, filtro de outlier |
| Dashboard/BI | Streamlit + pandas | exploração interativa, filtros, gráficos de tendência |
| Orquestração/infra | Docker + Docker Compose | scraper e dashboard como serviços separados, mesmo volume de dado |
| Notificação | Telegram Bot API | alerta em tempo real de oportunidade |
| IA aplicada (opcional) | Anthropic API | auditoria de qualidade de anúncio + resumo diário agentic sobre o próprio dado |

## Arquitetura

```
common/      -- config, schema (Pydantic) e storage (SQLite), compartilhados
scraper/     -- coleta: fetch -> parse -> valida -> sanidade -> diff -> grava -> alerta
dashboard/   -- Streamlit, só leitura do mesmo banco (pandas + SQL)
data/        -- banco SQLite (gitignored, criado em runtime)
```

Scraper e dashboard são dois serviços Docker independentes que **nunca se
comunicam diretamente** — trocam informação só pelo banco. Isso é uma
decisão deliberada de arquitetura de dado: o dashboard pode cair, ficar
lento ou ser reiniciado sem afetar a coleta, e vice-versa.

## Pipeline de dados

```
Fonte (OLX)
   |
   v
Coleta -- scraper/fetcher.py + parser.py
   |  requests simples (sem browser) lendo o JSON que a OLX já embute
   |  no HTML (payload Next.js) -- ver parser.py pro porquê disso ser
   |  mais robusto que raspar texto renderizado.
   v
Tratamento / validação -- common/schema.py (Pydantic)
   |  tipagem e regra de negócio por categoria (monitor, iPhone,
   |  computador) antes de qualquer linha tocar o banco. Dado
   |  malformado é rejeitado aqui, não silenciosamente aceito.
   v
Deduplicação e diff -- scraper/diff.py + common/storage.py
   |  upsert por chave única (listing_id + plataforma): 1 linha por
   |  anúncio, não 1 linha por rodada de coleta -- decisão de
   |  qualidade de dado, não só espaço em disco (ver por quê abaixo).
   v
Checkpoint de sanidade -- scraper/sanity.py
   |  antes de gravar, compara a rodada com o histórico (zero
   |  anúncios? maioria sem preço? queda abrupta?). Rodada suspeita
   |  é descartada -- nada é gravado -- e um alerta sai avisando.
   v
Armazenamento -- common/storage.py (SQLite, modo WAL)
   |  6 tabelas: anuncios, historico_precos, coletas, medianas_diarias,
   |  auditoria_ia, vendas.
   v
Análise estatística -- common/stats.py
   |  mediana (não média) por grupo comparável; filtro duplo de
   |  outlier/sucata/golpe (campo estruturado + regex sobre título);
   |  snapshot diário de mediana -> tendência de 30 dias.
   v
Indicador -- margem R$/%, direção de tendência, oportunidade sim/não
   v
Visualização -- dashboard/app.py + charts.py (Streamlit + pandas)
   |  filtros por categoria/UF/data, tendência de preço por grupo,
   |  aba de auditoria manual do dado bruto capturado.
   v
Ação -- scraper/notifier.py (alerta Telegram em tempo real) e
        scraper/resumo_diario.py (resumo diário agentic via LLM,
        opcional, sobre oportunidades + tendência do dia)
```

### Por que mediana, e por que dois filtros de outlier

A camada de análise (`common/stats.py`) compara cada anúncio com a
**mediana** do grupo comparável (ex.: monitor de mesma marca+tipo), não a
média — um anúncio de R\$1 de brincadeira ou um lote de loja profissional
não distorce a mediana do jeito que distorceria uma média.

Mas mediana sozinha não basta: um anúncio de peça quebrada por R\$40
comparado com a mediana de uma unidade funcionando gera uma "margem" falsa
gigante. A função `margem_e_confiavel()` usa **dois sinais independentes**
pra filtrar isso — o campo estruturado `condicao` da OLX **e** um regex
sobre o título — porque, na prática (visto ao vivo testando o dashboard),
vendedor preenche o campo estruturado errado com frequência (`"COM DEFEITO
NÃO LIGA"` no título, `condicao = "Usado - Excelente"` no campo). Um sinal
só deixava esse anúncio aparecer como "melhor oportunidade" do catálogo.

## Principais resultados

Números direto do banco de produção (consulta em 15/09/2026, janela de
coleta desde 23/08/2026):

| Métrica | Valor |
|---|---|
| Anúncios únicos rastreados | 4.322 |
| — iPhone | 2.709 |
| — computador completo | 809 |
| — monitor | 804 |
| Quedas de preço reais capturadas (histórico, não só estado atual) | 5.909 |
| Rodadas de coleta registradas (com checkpoint de sanidade cada uma) | 1.513 |
| Dias com snapshot de mediana gravado (`medianas_diarias`) | 6 |

**A decisão mais relevante do ponto de vista de análise de dado**: o
projeto começou coletando 3 categorias em paralelo de propósito — período
deliberado de "dado amplo antes de regra de negócio por categoria". Depois
de acompanhar monitor e computador completo por semanas, o próprio código
registra o motivo da mudança (`common/config.py`, commit
`remocao-de-computadores-e-monitores`):

> "monitor/computador pararam de ser agendados (mercado se mostrou
> ineficaz)"

Ou seja: **o dado coletado indicou que essas duas categorias não sustentam
a tese de arbitragem** (produto commodity, preço já convergido, sem
dispersão suficiente pra virar oportunidade real) — decisão tomada olhando
histórico real, não intuição — e o esforço foi redirecionado pra iPhone,
agora escalado pra **múltiplas regiões (UFs) na mesma execução**, cada uma
com seu próprio canal de alerta.

## Qualidade e governança de dado

Pontos que normalmente ficam escondidos e aqui estão documentados de
propósito (inclusive as falhas já encontradas e corrigidas):

- **Validação de schema na entrada** (Pydantic) — dado malformado é
  rejeitado antes de virar linha no banco, não filtrado depois.
- **Amostra mínima antes de confiar num indicador** — com menos de N
  anúncios distintos ativos num grupo, nenhuma mediana é reportada como
  confiável (nenhum "preço justo" fabricado sobre 2 ou 3 pontos).
- **Tendência de 30 dias não é forjada** — se o histórico disponível é
  menor que a janela pedida, o campo `mediana_periodo` volta `None` em vez
  de calcular sobre menos dias e rotular como "30 dias" mesmo assim.
- **Checkpoint de sanidade antes de gravar** — zero anúncios, maioria sem
  preço ou queda abrupta vs. histórico faz a rodada inteira ser descartada
  (nada é gravado) e dispara alerta — errar por cautela é mais barato que
  sujar o banco por dias sem ninguém notar.
- **Bug real de dado, corrigido**: cada anúncio parado no ar por N rodadas
  virava N amostras idênticas na mediana, inflando artificialmente o
  tamanho de amostra e enviesando o "preço justo" pro anúncio mais antigo
  no ar — corrigido mudando a granularidade pra 1 linha por anúncio (não
  por rodada de coleta). Ver `common/storage.py`.
- **Segundo bug real de dado, corrigido em 15/09/2026, achado rodando o
  sistema ao vivo**: a paginação da OLX às vezes repete um anúncio entre
  páginas diferentes da MESMA rodada (destaque reaparecendo, ou os
  resultados mudando de ordem entre uma requisição de página e outra).
  Isso gerava duas linhas de `historico_precos` com a mesma chave
  primária — violava a UNIQUE constraint e descartava **a rodada inteira**
  (nem os anúncios sem duplicata eram gravados), silenciosamente, com um
  alerta de erro no Telegram como único rastro. Reproduzido contra a OLX
  ao vivo (3 de 500 anúncios vieram duplicados numa única rodada),
  corrigido com dedup por `listing_id` em `upsert_ads()`
  (`common/storage.py`), com teste de regressão. Fechar esse tipo de bug é
  também o que sustenta o uptime medido abaixo — parte dos "buracos" de
  coleta provavelmente vinha daqui, não só da máquina hibernando.
- **Terceiro achado, de UX/confiabilidade do dashboard**: o seletor de
  categoria abria por padrão em "Monitor" — uma categoria descontinuada
  cujos anúncios ficam com `ativo=1` congelado (nunca mais são
  re-checados, já que a categoria parou de ser coletada). Resultado:
  margem calculada contra mediana **de dado morto**, produzindo números
  de 300-400% que pareciam erro de cálculo mas eram, na verdade, ausência
  de atualização. Corrigido trocando o padrão pra iPhone (a categoria
  ativa) e adicionando um aviso explícito quando Monitor/Computador/Todas
  é selecionado.
- **Limitação de infraestrutura documentada com número real, não
  estimativa**: a coleta roda numa máquina pessoal, não um servidor sempre
  ligado — uptime medido diretamente na tabela `coletas` (não é uma
  suposição), com buracos de dezenas de horas quando a máquina hiberna.
  Detalhe completo, com a query e a tabela de números, em
  `OLX_DEEP_DIVE.md`, seção 7.5. O próprio dashboard agora expõe esse
  número ao vivo (ver seção seguinte), em vez de deixá-lo só no código.

## Métricas do dashboard (frescor, ciclo de vida, distribuição)

Adicionadas em 15/09/2026, depois de olhar o dashboard e perceber que
"dado válido tecnicamente" e "dado confiável agora" são perguntas
diferentes — a segunda não tinha métrica nenhuma até então:

- **Frescor da coleta** — no topo, sempre visível: há quanto tempo foi a
  última rodada + uptime real dos últimos 7 dias (rodadas reais / rodadas
  esperadas, medido em `coletas`, não estimado). Trata "o dado tá
  atualizado?" como métrica de primeira classe, não como suposição.
- **Tempo médio no ar** — mediana de dias que um anúncio fica ativo antes
  de sumir da busca (`primeiro_visto_em` até `removido_em`), por categoria
  — métrica de ciclo de vida/giro de mercado.
- **Distribuição de preço** — histograma com mediana e média marcadas,
  por grupo filtrado. Explica visualmente por que `common/stats.py` usa
  mediana (robusta a outlier) em vez de média, e é também a primeira
  pista visual de quando uma categoria tem dado congelado — a mesma causa
  raiz do segundo bug listado acima.

## Como executar

```bash
cp .env.example .env
# edite o .env e preencha pelo menos TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
# (o projeto sobe sem isso, só fica sem notificação)

docker-compose up --build
```

Dashboard em `http://localhost:8501`. Rodar os testes localmente (sem
Docker, Python 3.11):

```bash
pip install -r scraper/requirements.txt -r dashboard/requirements.txt pytest
PYTHONPATH=. pytest tests/ -v
```

## Próximos passos

- **Mercado Livre como segunda plataforma** — a coluna `plataforma` já
  existe em todo o schema e no banco de propósito, pra isso não exigir
  migração quando chegar a hora.
- **Backtest formal** — usar `medianas_diarias` (mediana real do dia, não
  reconstruída depois) pra medir, com histórico suficiente, se os alertas
  de oportunidade de fato antecederam quedas de preço reais.
- **Camada de exportação/BI** — hoje a exploração é só via Streamlit; dar
  ao banco SQLite normalizado uma saída direta (CSV/Parquet agendado, ou
  conexão via ODBC) facilitaria plugar em Power BI/Excel sem reescrever a
  camada de análise.
- Roadmap completo de produto (bicicletas/ferramentas, produtização) no
  [`README.md`](../README.md#roadmap-próximos-passos-já-pensados-não-implementados).

# Diário de bordo

Registro de desenvolvimento por data: o que foi feito, o que os dados
mostraram, o que foi decidido e **o que ainda falta**. Feito pra quem retoma
o trabalho (pessoa ou agente) sem depender de memória de conversa. A
profundidade técnica está em [`OLX_DEEP_DIVE.md`](OLX_DEEP_DIVE.md); a leitura
por análise de dados, em [`CASE_DATA_ANALYTICS.md`](CASE_DATA_ANALYTICS.md).

Convenção: entrada mais recente no topo; toda afirmação numérica tem data.

---

## 12/11/2026 — pipeline com Prefect, camadas e modelo estrela

### Scraper ainda funciona?
Coleta de teste em 12/11/2026, ~08:17 UTC, só ES, só a página 1, banco SQLite
temporário (fora de `data/`), Telegram desligado. A primeira requisição voltou
**403**; a seguinte, segundos depois, **200**. Na rodada que passou: 57 itens no
array `ads`, **50 anúncios** (os outros 7 eram propaganda), 50 com preço,
checkpoint de sanidade ok. O parser não precisou de ajuste.

### O que foi feito
- `flows/pipeline_olx.py`: flow do Prefect (coletar → sanidade → diff → bronze →
  transformar → alertar), retry só em erro de rede, quarentena quando a sanidade
  reprova, aviso no Telegram pessoal quando o flow falha.
- `pipeline/`: armazenamento local/S3 (boto3 + `endpoint_url`), camadas
  bronze/prata, ouro em modelo estrela (`fato_anuncio_snapshot` + 3 dimensões) e 3
  consultas com window function.
- `scraper/coleta.py` e `scraper/alertas.py`: saíram de dentro do `main.py` pra o
  flow reaproveitar; o caminho antigo usa os mesmos módulos.
- **Bug corrigido:** quebra de parser (`ValueError` do `extract_ads`) escapava da
  rodada sem o aviso "possivelmente quebrado" no Telegram — o `except` estava em
  volta do `build_ads`, que não levanta esse erro.
- MinIO opcional no `docker-compose.yml` (perfil `lake`).
- Decisões em [`ADR_001_pipeline_prefect_camadas.md`](ADR_001_pipeline_prefect_camadas.md);
  perguntas de entrevista em [`perguntas_entrevista.md`](perguntas_entrevista.md).

### Testes
Antes: 160 passando. Depois: 204 passando no ambiente com Prefect 3.9.2 + boto3 +
moto; 197 passando e 2 módulos pulados (Prefect/boto3 ausentes) no Python do
sistema. Tudo em Python 3.14.7 — não testei em 3.11.

### AINDA FALTA
1. Subir o MinIO (Docker estava parado; conferir a imagem, a oficial `minio/minio`
   não aparecia no Docker Hub em 12/11/2026) e rodar o flow com `LAKE_BACKEND=s3`.
2. Testar o modo agendado (`--agendar` + `prefect server start`).
3. Decidir se o Prefect substitui o APScheduler no Docker.
4. Rodar a suíte em Python 3.11.
5. DDL Postgres da ouro: escrito, não executado.

---

## 20/09/2026 — de "dado que funciona" pra "história que se conta"

### Ponto de partida
Coleta rodando só no Espírito Santo, iPhone. Docs falavam em "múltiplas UFs" e
"mercado eficiente demais" — nenhum dos dois batia com o que o código coleta
nem com o que os dados mostram.

### Estado do banco (consulta de 20/09/2026)
4.807 anúncios únicos (iPhone 3.194 · computador 809 · monitor 804) · 6.517
linhas em `historico_precos` · ~1.600 rodadas em `coletas` · 8 dias de
`medianas_diarias` · coleta em 19 de 29 dias corridos (PC pessoal).

### Achados (calculados ao vivo na aba *Resumo* do dashboard)
1. **Dispersão ≠ oportunidade.** Monitor tinha 23% dos anúncios ≤75% da
   mediana do grupo, iPhone 5% — mas o grupo do monitor (marca+tipo) é
   heterogêneo; separar por polegadas derruba o CV de 0,43 pra 0,33. A decisão
   de descontinuar se mantém; o motivo ficou mais preciso.
2. **O limiar de 75% separa algo real.** iPhones ≤75% da mediana somem em ~1,9
   dia (n=115) contra ~3,0 nas demais faixas. Descritivo: venda e desanúncio se
   confundem, e há viés de sobrevivência.
3. **Negociação observada.** Queda real mediana de preço em iPhone: 6,3% (285
   anúncios, ~9%); o sistema assumia 10%. É proxy, não desconto de conversa.
   *(Um "8,3%" citado durante a sessão misturava as 3 categorias — o número
   correto pra iPhone é 6,3%.)*
4. **Qualidade.** 50 anúncios (1,6%) com título contradizendo o `modelo`.
5. **Margens de manchete exageradas.** 11 alertas de iPhone, 48–105% sobre o
   custo (mediana 67%). Causas: margem sobre custo (não sobre venda),
   comparação com mediana de preço PEDIDO, custos ausentes, preço baixo
   escondendo defeito ("leia descrição"), grupo sem controle de bateria/estado.

### O que foi construído (commits do dia)
| Commit | O quê |
|---|---|
| `05c65db` fix | exclui da mediana anúncio cujo título contradiz o modelo |
| `3544e48` feat | aba Resumo (dispersão, tempo no ar, qualidade) |
| `d3bf710` docs | escopo corrigido pra só ES; números atualizados |
| `51954bc` feat | margem em 3 leituras + ⚠️ margem suspeita (Telegram e dashboard) |
| `26a603f` feat | conferência de alertas, exportação BI, maiores buracos de coleta |
| `3fc63a2` docs | resumo de uma página no README; guia de coleta contínua |
| `c16ffdc` feat | campo de descrição na conferência + análise dos falsos alarmes |

Novos módulos: `common/insights.py` (análises puras), `common/export.py` e
`scripts/exportar_bi.py` (CSV pra BI), `dashboard/conferencias.py`. Suíte: 160
testes passando.

### Decisões
Registradas em `OLX_DEEP_DIVE.md`, seção 10, decisões 14 a 17 (margem em três
leituras, exclusão de conflito título×modelo, conferência manual, CSV pra BI).

### Como o sistema está rodando agora
`docker-compose` com scraper e dashboard reconstruídos no fim do dia (o
scraper já com o alerta novo). Dashboard em `http://localhost:8501`.

### AINDA FALTA
Ordem sugerida (impacto sobre esforço):

1. **Conferir 10–20 alertas** no anúncio real, colando a descrição (aba
   Oportunidades → "Conferi um alerta…"). Sem isso, não existe número de
   precisão — é o dado mais valioso pro post. Abaixo de 20, tratar como
   indicativo.
2. **Power BI**: importar `data/exports/*.csv` (gerar com
   `PYTHONPATH=. DB_PATH=data/olx_monitor.db python scripts/exportar_bi.py`).
   `dim_anuncios` liga aos `fato_*` por `listing_id`. Sugestão de painel: (a)
   visão geral e cobertura, (b) preço vs mediana por modelo, (c) alertas
   conferidos.
3. **Scraper ler a descrição do anúncio** — só vale depois de olhar a análise
   das descrições (o dashboard mostra quantos falsos a regra atual pegaria).
4. **Rever o piso R$ 600** (`orcamento_minimo_iphone`): exclui iPhones antigos
   legítimos (XR, 8, 7) da mediana e dos alertas. Decisão de negócio — não
   mexido.
5. **Prints** pro README e pro post (lista abaixo). Os links de imagem estão
   comentados no README até existirem os arquivos.
6. **Post do LinkedIn**: o rascunho foi escrito no chat (não está no repo) com
   números de 15/09 — atualizar pros de 20/09 acima e, idealmente, incluir a
   precisão dos alertas quando existir.
7. **Coleta contínua**: desativar a suspensão do PC (comando em
   `COLETA_CONTINUA.md`) pra subir a cobertura de dias.
8. **Backtest** (o alerta antecipou queda de preço?) — precisa de mais
   histórico em `medianas_diarias`.
9. **Antes de tornar o repositório público**: revisar o histórico do Git, e
   considerar revogar o token do bot no BotFather (`/revoke`) — ele apareceu
   em texto durante uma sessão de trabalho em 20/09/2026.

Pendências pequenas: avisos de LF/CRLF do Git (um `.gitattributes` resolve);
testes locais rodam em Python 3.14, enquanto o README recomenda 3.11.

### Imagens a capturar
- Alerta do Telegram (sem link de anúncio de terceiro).
- Dashboard → aba Resumo (gráficos de dispersão e de tempo no ar).
- Dashboard → aba Oportunidades (margens em três leituras).
- Power BI (quando existir).

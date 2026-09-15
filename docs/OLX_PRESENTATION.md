# OLX Monitor Intelligence — Apresentação do Projeto

> Visão geral do projeto em prosa corrida — para referência técnica completa e
> citações de código exatas, ver `OLX_DEEP_DIVE.md`. A versão em português vem
> primeiro; a versão em inglês não é tradução literal — é a mesma apresentação,
> reconstruída para soar natural em inglês.
>
> Revisado em 15/09/2026 para refletir o estado atual do projeto: iPhone é hoje
> a única categoria ativa, rodando em múltiplas UFs — monitor gamer e
> computador completo foram as categorias originais do MVP e permanecem no
> histórico do banco, mas pararam de ser coletadas depois que o próprio dado
> mostrou mercado eficiente demais pra sustentar a tese (detalhe na seção
> "Decisões técnicas" abaixo).

---

# Versão em português

## Problema

O problema que o projeto resolve é simples de descrever e difícil de fazer
manualmente: encontrar, na OLX, anúncios de usados vendidos abaixo do preço
justo de mercado — para comprar e revender com margem. A tese partiu de
experiência prévia vendendo iPhone e computador; o gargalo era tempo, não
conhecimento de mercado. Ficar atualizando a busca manualmente o dia inteiro
não escala, e o anúncio bom não fica no ar muito tempo.

## Motivação

A motivação de negócio, no fundo, é arbitragem informacional: uma fatia do
mercado de usados é ineficiente — vendedor com pressa, ou que simplesmente não
sabe quanto vale o que está vendendo, anuncia abaixo do preço justo. Não é uma
oportunidade permanente, é uma janela que fecha rápido: no início do projeto,
com o pouco histórico que já existia, um anúncio ficava no ar em média 15 a 16
horas antes de sumir. Contar com sorte de estar olhando na hora certa não é
estratégia.

## O que o sistema faz

O sistema roda sozinho, em background. A cada 12 minutos ele coleta os
anúncios ativos, calcula a mediana de preço de cada subgrupo de produto, e
dispara uma notificação no Telegram quando um anúncio aparece — ou cai de
preço — abaixo de 75% dessa mediana, dentro de um orçamento calibrado por
categoria. Um dashboard em Streamlit permite explorar os dados, ver tendência
de preço, e registrar manualmente o que foi de fato comprado e revendido, para
comparar a margem estimada com a margem real.

**Estado atual: iPhone, em múltiplas UFs.** O MVP testou três categorias em
paralelo (monitor gamer, iPhone, computador completo) por semanas — decisão
deliberada, para ter dado amplo antes de fechar regra de negócio por
categoria. Monitor e computador foram descontinuados depois que o dado
mostrou mercado eficiente demais pra sustentar a tese de arbitragem (mais
detalhe na seção "Decisões técnicas"). O esforço foi redirecionado pra
escalar iPhone geograficamente: hoje o scraper roda uma rodada sequencial por
estado configurado, cada um podendo notificar um grupo de Telegram diferente
— continuar testando a tese em novos mercados geográficos, em vez de novas
categorias de produto.

## Arquitetura

A arquitetura é propositalmente simples: dois serviços em Docker Compose. Um
scraper, que roda em background sem porta exposta, e um dashboard Streamlit,
que expõe a porta 8501. Os dois compartilham um módulo comum com a
configuração, o schema de dados e o acesso ao banco. Conversam entre si só
através de um arquivo SQLite — nunca se chamam diretamente. Isso funciona sem
erro de banco travado porque o SQLite roda em modo WAL, que permite um
processo escrevendo e outro lendo ao mesmo tempo.

## Scraping

Uma decisão técnica central: não há Playwright, Selenium, nem nenhum browser
automatizado. A OLX renderiza os anúncios no servidor e embute os dados como
JSON dentro do próprio HTML — é o payload de streaming do Next.js. Uma
requisição HTTP simples, com a biblioteca `requests`, já traz tudo que é
preciso, sem executar uma linha de JavaScript. Isso deixa a coleta mais leve,
mais rápida, e com uma pegada bem menor no site — relevante porque o objetivo
nunca foi burlar a proteção anti-bot da OLX, e sim ser discreto.

A parte mais delicada é extrair esse JSON: ele vem escapado como string dentro
do HTML, e o título de um anúncio às vezes tem colchete de verdade, tipo
"Monitor [PROMOÇÃO]" — um regex ingênuo quebra nesse caso. A solução conta
profundidade de colchete manualmente, caractere por caractere, e reaproveita o
próprio decoder de string do módulo `json` do Python pra desfazer o escape, em
vez de reimplementar isso na mão.

## Pipeline dos dados

Ponta a ponta: busca a página, extrai o JSON de anúncios, valida cada um com
Pydantic, roda um checkpoint de sanidade — que existe especificamente pra
pegar o dia em que a OLX mudar o site e quebrar o parser sem ninguém perceber
— grava no banco com upsert, calcula o que é novo ou caiu de preço, avalia se
vira oportunidade, e dispara o alerta. Se qualquer etapa falhar, a rodada é
abortada e um aviso chega no Telegram — nunca fica um erro silencioso
enchendo o banco de dado ruim.

## Banco de dados

É SQLite, não Postgres. A razão documentada no próprio código é o modo WAL:
permite um escritor e vários leitores simultâneos sem travar — exatamente o
padrão de uso real, um scraper escrevendo, um dashboard lendo. Uma
observação honesta: não existe uma comparação formal registrada em lugar
nenhum sobre por que não Postgres. Na prática, rodando tudo numa máquina só,
pessoal, um arquivo único sem precisar subir um serviço de banco à parte foi o
caminho mais simples — leitura de contexto, não uma decisão documentada na
hora.

## API/backend

Vale ser preciso aqui: o projeto **não** expõe uma API própria. Nenhum
endpoint HTTP recebendo requisição e devolvendo JSON. Ele consome duas
interfaces externas: a "superfície" de dados da OLX, que não é uma API
oficial, é o HTML público mesmo; e a API oficial do Telegram Bot, só o
endpoint de enviar mensagem, com `requests` puro, sem a biblioteca oficial de
bot — porque o sistema nunca precisa receber mensagem, só mandar.

## Docker

A containerização resolve um problema concreto, não é só padrão de mercado: o
Dockerfile trava a versão do Python em 3.11, porque as versões usadas de
`pydantic` e `pandas` não têm build pronta pra Python mais novo (testado na
prática: tentar instalar fora do Docker numa máquina com Python 3.14 reproduz
exatamente o erro esperado — falta de toolchain de C e Rust pra compilar do
zero). Dentro do Docker isso nunca acontece, porque a imagem sempre usa 3.11.

## Interface

O dashboard é Streamlit. Não existe uma justificativa escrita de por que
Streamlit e não React ou Flask — o critério foi tamanho do problema: é um
painel majoritariamente de leitura, com tabela, gráfico e formulário prontos,
sem necessidade de construir front-end do zero pra um projeto pessoal.

## Principais desafios

O maior desafio não foi construir o scraper — foi descobrir, testando contra
dado real, um conjunto de bugs que só apareciam com volume de verdade. Três
exemplos:

Primeiro, a versão original salvava uma linha nova no banco a cada rodada de
coleta, mesmo quando nada mudava — isso virou mais de 20 mil linhas para menos
de 300 anúncios distintos, e distorcia a mediana que decide o que é
oportunidade.

Segundo, testando o dashboard no navegador, um anúncio quebrado — literalmente
"não liga" — apareceu como a melhor oportunidade do catálogo, com margem de
mais de 800%, porque o campo de condição estruturado da OLX às vezes vem
errado: o vendedor descreve o defeito no título mas marca "excelente" no
formulário.

Terceiro, um anúncio novo sem preço detectado derrubava a rodada inteira em
silêncio, por causa de uma restrição `NOT NULL` no banco que esse caso violava.

Os três foram corrigidos e ganharam teste de regressão.

## Decisões técnicas

Cinco decisões que resumem bem o raciocínio por trás do projeto:

Usar mediana em vez de média pra calcular preço justo, porque é mais robusta a
outlier. Separar o "grupo de comparação" por categoria — marca e tipo pra
monitor, modelo e armazenamento pra iPhone, CPU e RAM pra computador — porque
marca sozinha não discrimina nada em iPhone ou computador. Usar dois sinais,
não um, pra excluir anúncio de sucata da mediana, porque um sinal só (o campo
estruturado sozinho) já deixou passar um defeito real pro dashboard antes.

E a decisão mais recente, de escopo de produto, não de código: depois de
semanas rodando monitor, iPhone e computador em paralelo — de propósito, pra
ter dado amplo antes de fechar regra por categoria —, o próprio código passou
a registrar, no commit que parou de agendar as duas primeiras, o motivo direto:
"mercado se mostrou ineficaz". Ou seja, a decisão de descontinuar duas
categorias e concentrar esforço em iPhone veio do dado coletado, não de
intuição inicial — e o esforço liberado foi pra escalar a categoria que
performou, geograficamente (múltiplas UFs), em vez de abrir uma quarta
categoria de produto.

## Limitações

As limitações, sem rodeio: os alertas têm partida fria — a mediana só é
confiável com pelo menos 5 anúncios no mesmo grupo, então uma região nova
(inclusive um novo estado de iPhone) não gera alerta nos primeiros dias. O
parser depende do formato atual do Next.js da OLX — se a OLX mudar de
framework, quebra, com erro claro, mas quebra. E a maior limitação hoje não é
de código, é de infraestrutura: o scraper roda num computador pessoal, e uma
medição direta nos timestamps do próprio banco (02/09/2026, período em que
todas as categorias ainda rodavam) mostrou uptime real entre 19% e 26%, bem
abaixo do que o intervalo configurado sugere — a mesma limitação estrutural
vale hoje pra coleta multi-UF de iPhone.

## Como escalaria

A resposta óbvia seria "colocar isso numa VPS". Essa opção já foi considerada e
descartada por um motivo específico: IP de datacenter tem taxa de bloqueio bem
maior que IP residencial num site com proteção anti-bot ativa, como a OLX.
Antes de qualquer nuvem, o primeiro componente que valeria mudar de verdade é
a disponibilidade da máquina atual — hoje ela dorme, e esse buraco é
silencioso: o Docker mostra o container como "rodando" o tempo todo, mesmo com
o host suspenso. Se um dia fizer sentido ir pra nuvem de verdade, o caminho já
mapeado é proxy residencial, não IP de datacenter puro. Depois disso, trocar
SQLite por Postgres só faria sentido com mais de um escritor simultâneo — hoje
seria over-engineering pro tamanho real do problema.

## Demonstração

Uma demonstração ao vivo mostraria: o dashboard aberto, a aba de oportunidades
já ordenada por margem estimada, com o filtro de UF de iPhone em ação; um
alerta real recebido no Telegram, com preço anunciado, custo depois de
negociar, e margem até a mediana; a suíte de testes rodando (dentro do Docker,
ou num venv com Python 3.11 — fora disso, `pydantic`/`pandas` não instalam
numa máquina com Python mais novo, limitação já coberta acima), com os 97
testes passando; e um commit específico no histórico onde a mensagem documenta
um bug real com número de antes e depois — por exemplo, 20 mil linhas virando
menos de 300 depois da correção de duplicação, ou o commit que descontinuou
monitor/computador citando o motivo direto no código.

## Próximos passos

O que já está planejado, mas não implementado por decisão de escopo, não por
falta de tempo: Mercado Livre como segunda plataforma — a coluna pra isso já
existe no banco desde o início, propositalmente, mas ainda não foi seguida por
falta de oportunidade de lucro clara ali; bicicleta e ferramenta elétrica como
categoria nova, reaproveitando quase todo o parser; um backtest formal usando
o histórico diário de mediana (`medianas_diarias`) pra medir se os alertas de
fato antecederam quedas de preço reais. E, mais pra frente, se a tese for
validada por alguns meses com números reais, considerar transformar o sistema
num produto pra outros revendedores — não antes disso.

---

# English version

*(Not a literal translation — the same overview, rebuilt to read naturally in
English.)*

## Problem

The problem this project solves is easy to describe and hard to do by hand:
find listings on OLX — Brazil's largest classifieds site — priced below fair
market value, in order to buy and resell for a margin. The thesis came from
prior experience reselling iPhones and computers; the real bottleneck was
time, not market knowledge. Refreshing a search page all day doesn't scale,
and a good listing doesn't stay up for long.

## Motivation

The business motivation, underneath it, is informational arbitrage: part of
the used-goods market is inefficient — sellers who are in a hurry, or who
genuinely don't know what their item is worth, list below fair value. That's
not a standing opportunity, it's a window that closes fast: early on, with the
little history available at the time, a listing disappeared after about 15 to
16 hours on average. Relying on luck — happening to be looking at the right
moment — isn't a real strategy.

## What the system does

The system runs completely unattended. Every 12 minutes it collects active
listings, computes the median price for each product subgroup, and sends a
Telegram alert whenever a listing shows up — or drops in price — below 75% of
that median, within a budget calibrated per category. A Streamlit dashboard
lets you explore the data, look at price trends, and manually log what was
actually bought and resold, to compare estimated margin against the real one.

**Current state: iPhone, across multiple states.** The MVP tested three
categories in parallel (gaming monitors, iPhones, full desktop computers) for
several weeks — a deliberate choice, to gather broad data before locking in
business rules per category. Monitors and computers were discontinued once the
data showed the market was too efficient to sustain an arbitrage thesis (more
in "Technical decisions" below). The freed-up effort went into scaling iPhone
geographically instead: the scraper now runs one sequential round per
configured state, each one able to notify a different Telegram group —
testing the same thesis in new geographic markets rather than new product
categories.

## Architecture

The architecture is deliberately simple: two Docker services. A scraper that
runs in the background with no exposed port, and a Streamlit dashboard on
port 8501. They share one common module for configuration, the data schema,
and database access. The two never talk to each other directly — the only
thing connecting them is a SQLite file. That works without lock errors
because SQLite runs in WAL mode, which lets one process write while another
reads at the same time.

## Scraping

A central technical decision: no Playwright, Selenium, or any headless
browser. OLX server-renders its listings and embeds the data as JSON right
inside the HTML — it's the Next.js streaming payload. A plain HTTP request
with the `requests` library already gets everything needed, with zero
JavaScript execution. That's lighter, faster, and leaves a much smaller
footprint on the site — relevant because the goal was never to bypass their
anti-bot protection, only to stay under the radar.

The tricky part is extracting that JSON: it comes escaped as a JS string
inside the HTML, and listing titles sometimes contain real brackets, like
"Monitor [SALE]" — a naive regex breaks on that. The solution tracks bracket
depth character-by-character, and reuses Python's own `json` string decoder to
undo the escaping, instead of reimplementing escape rules by hand.

## Data pipeline

End to end: fetch the page, extract the embedded ads JSON, validate each one
with Pydantic, run a sanity checkpoint — which exists specifically to catch
the day OLX changes their site and silently breaks the parser — write to the
database with an upsert, work out what's new or dropped in price, evaluate
whether it's a real opportunity, and fire the alert. If any step fails, that
round gets aborted and a Telegram warning goes out — nothing ever fails
silently and quietly fills the database with bad data.

## Database

It's SQLite, not Postgres. The reason that's actually documented in the code
is WAL mode — one writer, multiple readers, no locking — which is exactly the
real access pattern: one scraper writing, one dashboard reading. Worth being
upfront about: there's no formal comparison against Postgres written down
anywhere. In practice, running everything on a single personal machine, one
file with no separate database service to manage was the simplest path — a
read of the context, not a decision documented at the time.

## API / backend

Worth being precise here: this project doesn't expose an API of its own. No
HTTP endpoint takes a request and returns JSON. It consumes two external
interfaces instead: OLX's data surface, which isn't an official API, it's just
the public HTML; and the official Telegram Bot API, only the send-message
endpoint, using plain `requests` rather than the official bot library, since
the system never needs to receive messages, only send them.

## Docker

Containerizing solves a concrete problem, not just convention: the Dockerfile
pins Python to 3.11, because the pinned `pydantic` and `pandas` versions don't
have prebuilt wheels for a newer Python (tested in practice: installing
outside Docker on a machine with Python 3.14 reproduces exactly the expected
failure — missing C and Rust build toolchains to compile from source). That
never happens inside Docker, because the image always uses 3.11.

## Interface

The dashboard is Streamlit. There's no written justification for Streamlit
over React or Flask — the deciding factor was the size of the problem: it's a
mostly-read dashboard with tables, charts, and forms already built in, with no
need to hand-build a frontend for a personal project.

## Main challenges

The hardest part wasn't building the scraper, it was finding a set of bugs
that only showed up once real volume was flowing through it. Three worth
calling out:

First, the original version wrote a new database row on every collection
round, even when nothing changed — that turned into over 20,000 rows for
fewer than 300 distinct listings, and it was quietly skewing the median that
decides what counts as an opportunity.

Second, testing the dashboard in the browser surfaced a broken listing —
literally titled "doesn't turn on" — showing up as the best deal in the
catalog, with an 800%-plus margin, because OLX's structured condition field is
sometimes wrong: the seller describes the defect in the title but marks the
form as "excellent."

Third, a new listing with no detected price would silently crash the entire
round, because of a `NOT NULL` constraint downstream that case violated.

All three got fixed and got a regression test.

## Technical decisions

Five decisions that capture the reasoning behind the project:

Using the median instead of the mean for fair price, because it's more robust
to outliers. Splitting the "comparison group" per category — brand and type
for monitors, model and storage for iPhones, CPU and RAM for computers —
because brand alone doesn't discriminate anything for iPhones or computers.
Requiring two independent signals, not one, to exclude a broken item from the
median, because relying on the structured field alone already let a real
defective listing through to the dashboard once.

And the most recent decision, a scope call rather than a code one: after
weeks running monitors, iPhones, and computers in parallel — deliberately, to
gather broad data before locking in per-category business rules — the code
itself now records, in the commit that stopped scheduling the first two, the
direct reason: "market proved inefficient." In other words, the decision to
discontinue two categories and concentrate on iPhone came from the collected
data, not from the initial intuition — and the freed-up effort went into
scaling the category that worked geographically (multiple states), instead of
opening a fourth product category.

## Limitations

The limitations, stated plainly: alerts have a cold start — the median only
becomes trustworthy with at least 5 listings in the same group, so a new
region (including a new iPhone state) doesn't alert for the first few days.
The parser depends on OLX's current Next.js output format — if they change
frameworks, it breaks, loudly, but it breaks. And the biggest limitation today
isn't code, it's infrastructure: the scraper runs on a personal computer, and
a direct measurement against the database's own timestamps (02/09/2026, while
all categories were still running) showed real uptime between 19% and 26%,
well below what the configured interval implies — the same structural
limitation applies today to iPhone's multi-state collection.

## How it would scale

The obvious answer is "move it to a VPS." That option was already considered
and rejected for a specific reason: datacenter IPs get blocked at a much
higher rate than residential IPs on a site with active anti-bot protection,
like OLX. Before any cloud migration, the first component worth actually
changing is the uptime of the current machine — it sleeps today, and that gap
is silent: Docker shows the container as "running" the entire time, even while
the host itself is suspended. If a real move to the cloud ever makes sense,
the mapped-out path is a residential proxy, not a plain datacenter IP. After
that, swapping SQLite for Postgres would only make sense with more than one
concurrent writer — right now that would be over-engineering for the actual
size of the problem.

## Demo

A live walkthrough would show: the dashboard open, the opportunities tab
already sorted by estimated margin, with the iPhone state filter in action; a
real Telegram alert, with the listed price, the post-negotiation estimated
cost, and the margin against the group median; the test suite running (inside
Docker, or a Python 3.11 venv — outside that, `pydantic`/`pandas` won't
install on a newer Python, a limitation already covered above), all 97 tests
passing; and one specific commit where the message documents a real bug with
before-and-after numbers — for example, 20,000-plus rows collapsing to under
300 after the deduplication fix, or the commit that discontinued
monitors/computers citing the reason directly in the code.

## Next steps

What's already planned but not built yet, by scope choice rather than lack of
time: Mercado Livre as a second platform — the database column for that has
existed since day one, on purpose, but hasn't been pursued yet for lack of a
clear profit opportunity there; bikes and power tools as a new category,
reusing almost the entire parser; a formal backtest using the daily median
history (`medianas_diarias`) to measure whether alerts actually preceded real
price drops. And further out, if the thesis holds up over a few months of
real numbers, considering turning the system into a product for other
resellers — not before that.

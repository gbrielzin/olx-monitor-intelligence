# OLX Monitor Intelligence — Roteiro de Apresentação

> Isto é um roteiro pra **falar em voz alta**, não uma segunda documentação —
> pra referência técnica completa e citações de código exatas, use
> `OLX_DEEP_DIVE.md`. A versão em português vem primeiro; a versão em inglês
> **não é tradução literal** — é a mesma apresentação, reconstruída do zero pra
> soar como algo que dá pra falar de verdade numa entrevista.

---

# Versão em português

## Problema

O problema que eu queria resolver era simples de descrever e difícil de fazer
manualmente: encontrar anúncio de monitor gamer, iPhone ou computador usado,
na OLX da Grande Vitória, vendido abaixo do preço justo de mercado — pra eu
comprar e revender com margem. Eu já tinha vendido iPhone e computador antes,
então sabia que a tese funcionava; o problema era o tempo. Ficar atualizando a
busca manualmente o dia inteiro não escala, e o anúncio bom não fica no ar
muito tempo.

## Motivação

A motivação de negócio, no fundo, é arbitragem informacional: uma fatia do
mercado de usados é ineficiente — tem vendedor com pressa, ou que simplesmente
não sabe quanto vale o que está vendendo, e anuncia abaixo do preço justo. Isso
não é uma oportunidade permanente, é uma janela que fecha rápido. No começo do
projeto eu medi, com o pouco histórico que já existia, que um anúncio fica no
ar em média 15 a 16 horas antes de sumir. Ou seja, contar com sorte de estar
olhando na hora certa não é estratégia.

## O que o sistema faz

Hoje o sistema roda sozinho, em background. A cada 12 minutos ele coleta os
anúncios ativos de três categorias — monitor gamer, iPhone e computador
completo — calcula a mediana de preço de cada subgrupo de produto, e me manda
uma notificação no Telegram quando um anúncio aparece, ou baixa de preço,
abaixo de 75% dessa mediana, dentro de um orçamento que eu mesmo calibrei por
categoria. Também tenho um dashboard em Streamlit pra explorar os dados, ver
tendência de preço, e registrar manualmente o que eu de fato comprei e
revendi, pra comparar a margem estimada com a margem real.

## Arquitetura

A arquitetura é propositalmente simples: dois serviços em Docker Compose. Um
scraper, que roda em background sem porta exposta, e um dashboard Streamlit,
que expõe a porta 8501. Os dois compartilham um módulo comum com a
configuração, o schema de dados e o acesso ao banco. E eles conversam entre si
só através de um arquivo SQLite — nunca se chamam diretamente. Isso só
funciona sem erro de banco travado porque o SQLite roda em modo WAL, que
permite um processo escrevendo e outro lendo ao mesmo tempo.

## Scraping

Aqui tem uma decisão técnica que eu defendo bem: eu não uso Playwright, nem
Selenium, nem nenhum browser automatizado. A OLX renderiza os anúncios no
servidor e embute os dados como JSON dentro do próprio HTML — é o payload de
streaming do Next.js. Uma requisição HTTP simples, com a biblioteca
`requests`, já traz tudo que eu preciso, sem executar uma linha de
JavaScript. Isso deixa a coleta mais leve, mais rápida, e com uma pegada bem
menor no site — o que importa porque eu não tento burlar a proteção anti-bot
da OLX, eu quero ser discreto.

A parte mais delicada é extrair esse JSON: ele vem escapado como string
dentro do HTML, e o título de um anúncio às vezes tem colchete de verdade,
tipo "Monitor [PROMOÇÃO]" — um regex ingênuo quebra nesse caso. Eu resolvo
contando profundidade de colchete manualmente, caractere por caractere, e
reaproveito o próprio decoder de string do módulo `json` do Python pra
desfazer o escape, em vez de reimplementar isso na mão.

## Pipeline dos dados

Ponta a ponta: busca a página, extrai o JSON de anúncios, valida cada um com
Pydantic, roda um checkpoint de sanidade — que existe especificamente pra
pegar o dia em que a OLX mudar o site e quebrar meu parser sem eu perceber —
grava no banco com upsert, calcula o que é novo ou caiu de preço, avalia se
vira oportunidade, e dispara o alerta. Se qualquer etapa falhar, a rodada é
abortada e eu recebo um aviso no Telegram — nunca fica um erro silencioso
enchendo o banco de dado ruim.

## Banco de dados

É SQLite, não Postgres. A razão documentada no próprio código é o modo WAL:
permite um escritor e vários leitores simultâneos sem travar — exatamente o
meu padrão de uso, um scraper escrevendo, um dashboard lendo. Vou ser honesto
se me perguntarem por que não Postgres: eu não tenho uma comparação formal
registrada em lugar nenhum. Na prática, rodando tudo numa máquina só, pessoal,
um arquivo único sem precisar subir um serviço de banco à parte foi o caminho
mais simples — mas essa é minha leitura do contexto, não uma decisão que eu
documentei na hora.

## API/backend

Preciso ser preciso aqui, porque é fácil escorregar: o projeto **não** expõe
uma API própria. Nenhum endpoint HTTP recebendo requisição e devolvendo JSON.
Ele consome duas interfaces externas: a "superfície" de dados da OLX, que não
é uma API oficial, é o HTML público mesmo; e a API oficial do Telegram Bot, só
o endpoint de enviar mensagem, com `requests` puro, sem a biblioteca oficial
de bot — porque eu nunca preciso receber mensagem, só mandar.

## Docker

Eu container izo por um motivo concreto, não só por padrão de mercado: o
Dockerfile trava a versão do Python em 3.11, e isso resolve um problema real
— as versões que eu uso de `pydantic` e `pandas` não têm build pronta pra
Python mais novo, tipo o 3.14 que eu tenho localmente. Eu testei isso na
prática: tentei instalar fora do Docker, e deu exatamente o erro que eu
esperava — falta de toolchain de C e Rust pra compilar do zero. Dentro do
Docker isso nunca acontece, porque a imagem sempre usa 3.11.

## Interface

O dashboard é Streamlit. Eu não tenho uma justificativa escrita de por que
Streamlit e não React ou Flask — então numa entrevista eu sou direto sobre
isso: foi a ferramenta certa pro tamanho do problema. É um painel
majoritariamente de leitura, com tabela, gráfico e formulário prontos, e eu
não precisava construir front-end do zero pra um projeto pessoal.

## Principais desafios

O maior desafio não foi construir o scraper — foi descobrir, testando contra
dado real, um conjunto de bugs que só apareciam com volume de verdade. Três
exemplos que eu defendo bem:

Primeiro, a versão original salvava uma linha nova no banco a cada rodada de
coleta, mesmo quando nada mudava — isso virou mais de 20 mil linhas pra menos
de 300 anúncios distintos, e distorcia a mediana que decide o que é
oportunidade.

Segundo, testando o dashboard no navegador, eu vi um anúncio quebrado,
literalmente "não liga", aparecendo como a melhor oportunidade do catálogo,
com margem de mais de 800% — porque o campo de condição estruturado da OLX às
vezes vem errado: o vendedor descreve o defeito no título mas marca
"excelente" no formulário.

Terceiro, um anúncio novo sem preço detectado derrubava a rodada inteira em
silêncio, por causa de uma restrição `NOT NULL` no banco que esse caso
violava.

Os três foram corrigidos e ganharam teste de regressão.

## Decisões técnicas

Se eu tivesse que escolher três decisões pra defender numa entrevista, seriam
essas: usar mediana em vez de média pra calcular preço justo, porque é mais
robusta a outlier; separar o "grupo de comparação" por categoria — marca e
tipo pra monitor, modelo e armazenamento pra iPhone, CPU e RAM pra computador
— porque marca sozinha não discrimina nada em iPhone ou computador; e usar
dois sinais, não um, pra excluir anúncio de sucata da mediana, porque um sinal
só (o campo estruturado sozinho) já deixou passar um defeito real pro
dashboard antes.

## Limitações

As limitações eu assumo sem rodeio: os alertas têm partida fria — a mediana só
é confiável com pelo menos 5 anúncios no mesmo grupo, então categoria nova não
gera alerta nos primeiros dias. O parser depende do formato atual do Next.js
da OLX — se a OLX mudar de framework, quebra, com erro claro, mas quebra. E a
maior limitação hoje não é de código, é de infraestrutura: o scraper roda no
meu PC pessoal, e eu medi o uptime real cruzando os timestamps do próprio
banco — fica entre 19% e 26%, bem abaixo do que o intervalo configurado
sugere.

## Como eu escalaria

A resposta óbvia seria "eu botaria isso numa VPS". Eu já considerei isso e
descartei, por um motivo específico, não por preguiça: IP de datacenter tem
taxa de bloqueio bem maior que IP residencial num site com proteção anti-bot
ativa, como a OLX. Então antes de qualquer nuvem, o primeiro componente que eu
mudaria de verdade é a disponibilidade da máquina atual — hoje ela dorme, e
esse buraco é silencioso: o Docker mostra o container como "rodando" o tempo
todo, mesmo com o host suspenso. Se um dia eu for pra nuvem de verdade, o
caminho já mapeado é proxy residencial, não IP de datacenter puro. Depois
disso, eu só trocaria SQLite por Postgres se precisasse de mais de um escritor
ao mesmo tempo — hoje isso seria over-engineering pro tamanho real do
problema.

## Demonstração

Numa demonstração ao vivo eu mostraria: o dashboard aberto, a aba de
oportunidades já ordenada por margem estimada; um alerta real recebido no
Telegram, com preço anunciado, custo depois de negociar, e margem até a
mediana; a suíte de testes rodando (dentro do Docker, ou num venv com Python
3.11 — fora disso, `pydantic`/`pandas` não instalam numa máquina com Python
mais novo, eu confirmo isso na seção de limitações), com os 97 testes
passando; e um commit específico no histórico onde a mensagem documenta um bug
real com número de antes e depois — por exemplo, 20 mil linhas virando menos
de 300 depois da correção de duplicação.

*(Nota pra mim mesmo: rodar a suíte de verdade antes de qualquer entrevista —
"eu tenho 97 testes" só é uma afirmação defensável se eu já vi todos passando
com meus próprios olhos pouco antes, não porque o README ou este documento
dizem que deveriam passar.)*

## Próximos passos

O que já está planejado, mas não implementado por decisão minha, não por
falta de tempo: mais termos de busca dentro das categorias que já existem;
Mercado Livre como segunda plataforma — a coluna pra isso já existe no banco
desde o início, propositalmente, mas eu decidi não seguir por enquanto porque
não vi oportunidade clara de lucro lá; bicicleta e ferramenta elétrica como
quarta categoria, reaproveitando quase todo o parser. E, mais pra frente, se
eu validar a tese pessoalmente por alguns meses e os números fizerem sentido,
considerar transformar isso num produto pra outros revendedores — não antes
disso.

---

# English version

*(Not a literal translation — the same talk, rebuilt to actually be spoken.)*

## Problem

The problem I wanted to solve was easy to describe and hard to do by hand:
find listings for gaming monitors, iPhones, and used desktop computers on
OLX — Brazil's biggest classifieds site, in my metro area — priced below fair
market value, so I could buy and resell for a margin. I'd already sold
iPhones and computers before, so I knew the underlying thesis worked. The real
problem was time: refreshing a search page all day doesn't scale, and a good
listing doesn't stay up for long.

## Motivation

The business motivation, underneath it, is informational arbitrage: part of
the used-goods market is inefficient — sellers who are in a hurry, or who
genuinely don't know what their item is worth, list below fair value. That's
not a standing opportunity, it's a window that closes fast. Early on, with the
little history I had, I measured that a listing disappears after about 15 to
16 hours on average. So relying on luck — happening to be looking at the right
moment — isn't a real strategy.

## What the system does

Today the system runs completely unattended. Every 12 minutes it collects
active listings across three categories — gaming monitors, iPhones, and full
desktop computers — computes the median price for each product subgroup, and
sends me a Telegram alert whenever a listing shows up, or drops in price,
below 75% of that median and within a budget I calibrated per category.
There's also a Streamlit dashboard where I can explore the data, look at price
trends, and manually log what I actually bought and resold, to compare my
estimated margin against the real one.

## Architecture

The architecture is deliberately simple: two Docker services. A scraper that
runs in the background with no exposed port, and a Streamlit dashboard on
port 8501. They share one common module for configuration, the data schema,
and database access. And the two never talk to each other directly — the only
thing connecting them is a SQLite file. That only works without lock errors
because SQLite runs in WAL mode, which lets one process write while another
reads at the same time.

## Scraping

This is one trade-off I can defend well: I don't use Playwright, Selenium, or
any headless browser. OLX server-renders its listings and embeds the data as
JSON right inside the HTML — it's the Next.js streaming payload. So a plain
HTTP request with the `requests` library already gets me everything I need,
with zero JavaScript execution. That's lighter, faster, and leaves a much
smaller footprint on the site — which matters, because I'm not trying to
bypass their anti-bot protection, I want to stay under the radar.

The tricky part is extracting that JSON: it comes escaped as a JS string
inside the HTML, and listing titles sometimes contain real brackets, like
"Monitor [SALE]" — a naive regex breaks on that. I handle it by tracking
bracket depth character-by-character, and I reuse Python's own `json` string
decoder to undo the escaping, instead of reimplementing escape rules by hand.

## Data pipeline

End to end, the pipeline is: fetch the page, extract the embedded ads JSON,
validate each one with Pydantic, run a sanity checkpoint — which exists
specifically to catch the day OLX changes their site and silently breaks my
parser — write to the database with an upsert, work out what's new or
dropped in price, evaluate whether it's a real opportunity, and fire the
alert. If any step fails, that round gets aborted and I get a Telegram
warning — nothing ever fails silently and quietly fills the database with bad
data.

## Database

It's SQLite, not Postgres. The reason that's actually documented in the code
is WAL mode — one writer, multiple readers, no locking — which is exactly my
access pattern: one scraper writing, one dashboard reading. I'll be upfront if
asked why not Postgres: I don't have a formal comparison written down
anywhere. In practice, running everything on a single personal machine, one
file with no separate database service to manage was the simplest path — but
that's my own read of the context, not a decision I documented at the time.

## API / backend

I need to be precise here, because it's an easy thing to overstate: this
project doesn't expose an API of its own. No HTTP endpoint takes a request and
returns JSON — neither the scraper nor the dashboard. It consumes two external
interfaces instead: OLX's data surface, which isn't an official API, it's just
the public HTML; and the official Telegram Bot API, only the send-message
endpoint, using plain `requests` rather than the official bot library, because
I never need to receive messages, only send them.

## Docker

I containerize for a concrete reason, not just convention: the Dockerfile
pins Python to 3.11, and that solves a real problem — the pydantic and pandas
versions I depend on don't have prebuilt wheels for a newer Python, like the
3.14 I actually have installed locally. I tested this myself: I tried
installing outside Docker, and got exactly the failure I expected — missing C
and Rust build toolchains to compile from source. That never happens inside
Docker, because the image always uses 3.11.

## Interface

The dashboard is Streamlit. I don't have a written justification for
Streamlit over React or Flask, so I'm upfront about that in an interview too
— it was the right-sized tool for the problem. It's a mostly-read dashboard
with tables, charts, and forms already built in, and I didn't need to
hand-build a frontend for a personal project.

## Main challenges

The hardest part wasn't building the scraper, it was finding a set of bugs
that only showed up once there was real volume flowing through it. Three I
can defend well:

First, the original version wrote a new database row on every collection
round, even when nothing changed — that turned into over 20,000 rows for
fewer than 300 distinct listings, and it was quietly skewing the median that
decides what counts as an opportunity.

Second, testing the dashboard in the browser, I saw a broken listing —
literally titled "doesn't turn on" — showing up as the best deal in the
catalog, with an 800%-plus margin, because OLX's structured condition field is
sometimes wrong: the seller describes the defect in the title but marks the
form as "excellent."

Third, a new listing with no detected price would silently crash the entire
round, because of a `NOT NULL` constraint downstream that case violated.

All three got fixed and got a regression test.

## Technical decisions

If I had to pick three trade-offs to defend, they'd be: using the median
instead of the mean for fair price, because it's more robust to outliers;
splitting the "comparison group" per category — brand and type for monitors,
model and storage for iPhones, CPU and RAM for computers — because brand alone
doesn't discriminate anything for iPhones or computers; and requiring two
independent signals, not one, to exclude a broken item from the median,
because relying on the structured field alone already let a real defective
listing through to the dashboard once.

## Limitations

The limitations I own without hedging: alerts have a cold start — the median
only becomes trustworthy with at least 5 listings in the same group, so a new
category doesn't alert for the first few days. The parser depends on OLX's
current Next.js output format — if they change frameworks, it breaks, loudly,
but it breaks. And the biggest limitation today isn't code, it's
infrastructure: the scraper runs on my personal computer, and I measured real
uptime by cross-referencing timestamps in my own database — it comes out to
somewhere between 19% and 26%, well below what the configured interval
implies.

## How I'd scale it

The obvious answer is "I'd move it to a VPS." I already considered that and
rejected it, for a specific reason, not out of laziness: datacenter IPs get
blocked at a much higher rate than residential IPs on a site with active
anti-bot protection, like OLX. So before any cloud migration, the first
component I'd actually change is the uptime of the current machine — it sleeps
today, and that gap is silent: Docker shows the container as "running" the
entire time, even while the host itself is suspended. If I ever do move to
the cloud for real, the path I've already mapped out is a residential proxy,
not a plain datacenter IP. After that, I'd only swap SQLite for Postgres if I
needed more than one concurrent writer — right now that would be
over-engineering for the actual size of the problem.

## Demo

In a live walkthrough I'd show: the dashboard open, the opportunities tab
already sorted by estimated margin; a real Telegram alert, with the listed
price, the post-negotiation estimated cost, and the margin against the group
median; the test suite running (inside Docker, or a Python 3.11 venv — outside
that, `pydantic`/`pandas` won't install on a newer Python, which is one of my
documented limitations), all 97 tests passing; and one specific commit where
the message documents a real bug with before-and-after numbers — for example,
20,000-plus rows collapsing to under 300 after the deduplication fix.

*(Note to self: actually run the suite before any interview — "I have 97
tests" is only a defensible claim once I've watched them pass myself, not
because the README or this document says they should.)*

## Next steps

What's already planned but deliberately not built yet — by choice, not lack
of time: more search terms within the categories I already have; Mercado
Livre as a second platform — the database column for that has existed since
day one, on purpose, but I decided not to pursue it for now because I didn't
see a clear profit opportunity there; bikes and power tools as a fourth
category, reusing almost the entire parser. And further out, if I validate
the thesis personally for a few months and the numbers hold up, considering
turning this into a product for other resellers — not before that.

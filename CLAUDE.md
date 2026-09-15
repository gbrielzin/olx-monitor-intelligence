# Instruções para o Claude neste repositório

## Git — nunca mexer sozinho

**Claude não executa comandos git neste projeto.** Isso inclui `git add`,
`git commit`, `git push`, `git checkout`, criar/trocar branch, ou qualquer
outra operação de git — mesmo que o usuário peça algo que normalmente
levaria a um commit (ex.: "termina essa mudança"), e mesmo dentro de um
subagente/fork lançado a partir desta sessão (o subagente herda esta regra
também). Ler o repositório (`git status`, `git diff`, `git log`, `git
show`) é permitido, só **não executar operações que alteram o estado do
git**.

Se uma tarefa parecer terminar num commit, edite os arquivos e avise que
está pronto pra revisão — o usuário mesmo decide quando e o que comitar.

**Única exceção**, e só quando o usuário confirmar no momento: vazamento
de credencial/segredo já commitado, ou algo que comprometa a segurança do
projeto de verdade. Mesmo nesse caso, confirmar com o usuário antes de
agir — não presumir a exceção sozinho.

(Incidente registrado em 15/09/2026: um subagente lançado nesta sessão
comitou e deu push sem autorização, contrariando o usuário ter dito
explicitamente "ainda não". Checado depois: o commit não continha
segredo nenhum, só documentação — por isso a exceção acima não se
aplicou e nada foi revertido automaticamente.)

## Contexto do projeto

Sistema de arbitragem informacional que monitora anúncios usados na OLX.
Hoje ativo pra iPhone, em múltiplas UFs. Monitor gamer e computador
completo foram testados e descontinuados (mercado se mostrou ineficaz —
ver `common/config.py` e `CASE_DATA_ANALYTICS.md`).

- `README.md` — visão geral e como rodar.
- `CASE_DATA_ANALYTICS.md` — o projeto pelo ângulo de análise de dados
  (pipeline, qualidade, indicadores), voltado pra candidatura em vaga de
  Data Analytics.
- `OLX_DEEP_DIVE.md` — documentação técnica aprofundada (arquitetura,
  scraping, banco, decisões).
- `OLX_PRESENTATION.md` — apresentação do projeto em prosa (PT/EN).

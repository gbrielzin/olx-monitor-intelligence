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

(Nota de 15/09/2026: cheguei a suspeitar de commits "fantasma" nesse dia —
apareceram commits que eu não tinha rodado. Investigado e esclarecido
pelo usuário: era ele mesmo commitando/dando push do próprio trabalho em
paralelo, fora desta sessão. Não era automação descontrolada. A regra
acima continua valendo do mesmo jeito — só registrando que esse
"mistério" específico já foi resolvido, não precisa reabrir.)

## Fim de tarefa — sugestão de commit (sempre, no final)

Como Claude nunca executa git aqui, é o usuário quem decide e roda o
commit. Pra isso ficar automático pra ele, **toda vez que uma tarefa
terminar com arquivo alterado**, fechar a resposta com um bloco assim:

```
---
📦 Commit?
Momento: [bom momento -- mudança coerente e testada | melhor esperar -- motivo]
Tipo sugerido: feat | fix | docs | refactor | test | chore
Mensagem sugerida: <tipo>: <resumo curto em português, minúsculo, sem ponto final>
Arquivos: <lista curta, ou "todos os alterados" se for tudo>
```

Regras pra montar o bloco:

- **Tipo** segue Conventional Commits, adaptado ao vocabulário que já
  aparece no `git log` deste repo (mensagens em português, tipo em
  inglês): `feat` (funcionalidade nova, ex.: métrica nova no dashboard),
  `fix` (bug corrigido), `docs` (só documentação/README/CLAUDE.md),
  `refactor` (reorganização sem mudar comportamento, ex.: mover arquivo
  pra `docs/`), `test` (só teste), `chore` (config, `.gitignore`,
  dependências).
- **"Bom momento"** = os testes relevantes passaram, a mudança é
  autocontida (não é "meio de uma refatoração"), e não mistura duas
  preocupações não relacionadas. Se misturar (ex.: um bug fix junto com
  uma feature nova), sugerir **dois blocos separados** em vez de um só —
  mais fácil reverter um sem o outro depois.
- **"Melhor esperar"** = ainda tem teste falhando, mudança pela metade, ou
  arquivo tocado só pra investigação/diagnóstico (não é pra virar commit).
- Nunca sugerir incluir `.env`, `data/*.db` ou qualquer coisa que o
  `.gitignore` já bloqueia — se aparecer como "untracked" pronto pra ir,
  avisar antes de sugerir o commit, não depois.

## Contexto do projeto

Sistema de arbitragem informacional que monitora anúncios usados na OLX.
Hoje ativo pra iPhone, só no ES (código suporta múltiplas UFs). Monitor gamer e computador
completo foram testados e descontinuados (mercado se mostrou ineficaz —
ver `common/config.py` e `docs/CASE_DATA_ANALYTICS.md`).

Documentação em `docs/` (README e CLAUDE.md ficam na raiz de propósito --
convenção de ferramenta, não mover):

- `README.md` — visão geral e como rodar.
- `docs/CASE_DATA_ANALYTICS.md` — o projeto pelo ângulo de análise de
  dados (pipeline, qualidade, indicadores), voltado pra candidatura em
  vaga de Data Analytics.
- `docs/OLX_DEEP_DIVE.md` — documentação técnica aprofundada (arquitetura,
  scraping, banco, decisões).
- `docs/OLX_PRESENTATION.md` — apresentação do projeto em prosa (PT/EN).
- `docs/DIARIO_DE_BORDO.md` — diário de desenvolvimento por data: o que foi
  feito, achados, decisões e a lista do que ainda falta. **Ler primeiro ao
  retomar o trabalho** e atualizar no fim de cada sessão.
- `docs/COLETA_CONTINUA.md` — como aumentar a cobertura da coleta (PC
  pessoal vs. VPS).

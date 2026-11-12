# Instruções para o Claude neste repositório

## Git — Claude pode commitar (regra nova de 12/11/2026)

O Gabriel liberou: **Claude pode rodar git neste projeto** — `add`,
`commit`, criar/trocar branch e `push` de branch de trabalho. Vale também
pra subagente lançado desta sessão.

Regras que continuam:

- **Autor é sempre o Gabriel.** Nunca colocar `Co-Authored-By`,
  "Generated with Claude" ou qualquer menção a Claude/IA na mensagem de
  commit, no corpo do PR ou como autor. Usar o `user.name`/`user.email`
  já configurados no git dele.
- Commits pequenos e coerentes (uma preocupação por commit), mensagem em
  português no padrão `<tipo>: <resumo minúsculo sem ponto final>`
  (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`).
- Só commitar com os testes relevantes passando.
- Nunca commitar `.env`, `data/*.db`, dados coletados ou segredos.
- **Merge no `master` e `push --force` só com ok explícito do Gabriel
  no momento.** Push de branch de trabalho está liberado.

(Nota de 15/09/2026: os commits "fantasma" daquele dia eram o próprio
Gabriel commitando em paralelo. Assunto resolvido.)

## Fim de tarefa — resumo do commit

Ao terminar uma tarefa com arquivo alterado, ou Claude faz o commit
(seguindo as regras acima) e informa hash + mensagem, ou, se for "melhor
esperar" (teste falhando, mudança pela metade, arquivo só de
diagnóstico), avisa o motivo e não commita.

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

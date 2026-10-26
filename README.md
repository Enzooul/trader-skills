# Trader Skills

Skills do Claude Code (`trader-*`) usadas pela automação de day trading do usuário, espelhadas aqui para que as rotinas em nuvem (claude.ai/code/routines) consigam clonar este repositório e descobrir as skills em `.claude/skills/`.

A fonte de verdade continua sendo `~/.claude/skills/` na máquina local do usuário — atualize este repo manualmente quando as skills locais mudarem.

## Skills de terceiros

Duas skills de apoio vêm do repositório [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) (licença MIT, ver `LICENSE-claude-skills`), copiadas do commit `27cfcd0`:

| Skill | Para que serve aqui |
|---|---|
| `statistical-analyst` | Testes de hipótese, intervalos de confiança e tamanho de amostra — útil para checar se o resultado de um setup ou da Time Machine é estatisticamente real ou sorte. |
| `data-quality-auditor` | Audita dados (lacunas, outliers, inconsistências) — útil para validar barras e cotações antes de confiar nelas. |

Para atualizar, copie de novo as pastas `engineering/statistical-analyst/skills/statistical-analyst` e `engineering/data-quality-auditor/skills/data-quality-auditor` do repositório de origem.

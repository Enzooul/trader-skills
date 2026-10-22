# Kit de skills para os bate-papos do claude.ai

No Claude Code, cada repositório-oficina carrega as próprias skills. Nos bate-papos do claude.ai é diferente: **toda skill ativada aparece em todas as conversas**. Por isso este kit é enxuto: 28 skills escolhidas entre as 171 das oficinas.

Cada ZIP é uma skill pronta para upload. A descrição ganhou **gatilhos em português** no final, para o Claude reconhecer pedidos escritos em português. Em 4 skills (`trader-risk`, `stock-analysis`, `research`, `andreessen`) uma frase de detalhe da descrição foi retirada para caber no limite de 1.024 caracteres. O conteúdo da skill não mudou.

## Como instalar

1. Abra o claude.ai no navegador (computador, de preferência).
2. Vá em **Configurações → Capacidades** e confirme que a **execução de código** está ativada. As skills precisam dela.
3. Na seção **Skills**, use **Enviar skill** (upload) e escolha um ZIP. Repita para cada um.
4. Depois de enviadas, as skills podem ser ligadas e desligadas na mesma tela.

Os nomes dos menus podem estar um pouco diferentes na sua versão. Não confirmei se o upload funciona pelo app de celular.

## O que tem no kit

| Área | Skills | Observação |
|---|---|---|
| Trading | `trader-scanner`, `trader-news`, `trader-entry`, `trader-exit`, `trader-timemachine`, `trader-budget`, `trader-risk`, `trader-broker`, `trader-alerts`, `trader-analyst`, `trader-pelosi`, `statistical-analyst` | Funcionam melhor no Claude Code. No chat, dependem de o ambiente de código conseguir acessar a internet (Yahoo, SEC, Câmara dos EUA). Os arquivos que elas gravam (`trader/*.json`) não persistem entre conversas. `trader-broker` precisa do conector da corretora. |
| Finanças | `stock-analysis`, `financial-analyst`, `business-investment-advisor` | |
| Pesquisa | `research`, `pulse`, `dossier`, `deepread` | A conta já tem uma `deep-research` da Anthropic, por isso ela não entrou. |
| Negócios | `roast`, `andreessen` | |
| Marketing | `marketing-ops`, `content-production`, `copywriting`, `content-humanizer` | Para trabalho pesado de marketing, use a oficina `marketing-skills` no Claude Code. |
| Produtividade | `deep-work`, `weekly-review`, `capture` | |

## Dica

Comece só com as áreas que usa toda semana. Quanto menos skills ativas, melhor o Claude escolhe. As outras você liga quando precisar.

Licença: as skills que não são `trader-*` vêm de [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) (MIT, ver `../LICENSE-claude-skills`).

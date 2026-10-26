# Trader Skills

Este repositório é a oficina de **day trading** (ações dos EUA, só compra/long). As skills `trader-*` formam uma linha de montagem; siga a ordem abaixo e passe o resultado de uma para a próxima. As skills ficam em `.claude/skills/`.

## Como responder

- O usuário escreve em **português do Brasil**: responda sempre em português, mesmo que a skill esteja escrita em inglês. Termos técnicos podem ficar em inglês quando não houver tradução comum, com uma explicação curta na primeira vez.
- O usuário tem conhecimento **intermediário**: comece com uma explicação curta do que vai ser feito e por quê, depois entregue o trabalho principal. Textos claros, completos e didáticos.
- Em estratégias e planos, seja o mais avançado e completo possível.
- **Não invente informações.** Se um dado não puder ser verificado (preço, número, fato, fonte), diga que não sabe ou que está no limite do seu conhecimento.

## Como escolher a skill

- Os pedidos chegam em português; as descrições das skills estão em inglês. Use o mapa abaixo para traduzir o pedido na skill certa.
- Se o pedido não bater claramente com uma skill, comece pelo **roteador** indicado no mapa: ele escolhe a especialista.
- Diga ao usuário, em uma linha, qual skill está usando e por quê.

## Arquivos gerados

Sessões na nuvem são apagadas ao terminar. Quando uma skill gerar um arquivo que valha guardar (relatório, plano, contexto), salve-o dentro deste repositório e ofereça fazer commit.

## Mapa de pedidos

Ordem do dia: `trader-scanner` → `trader-news` → `trader-entry` → `trader-exit` → `trader-timemachine` → `trader-budget` → `trader-risk` → `trader-broker`, com `trader-alerts` vigiando stop, alvo e horário.

| Pedido em português | Skill |
|---|---|
| "procura setups", "o que está rompendo", "monta minha watchlist", "scan pré-mercado" | `trader-scanner` |
| "tem notícia de X?", "por que está subindo/caindo?", "resumo da manhã" | `trader-news` |
| "me avisa quando entrar", "rompeu a abertura?", "vigia TSLA" | `trader-entry` |
| "onde ponho o stop?", "qual o alvo?", "risco/retorno" | `trader-exit` |
| "quais as chances?", "simula esse trade", "backtest" | `trader-timemachine` |
| "quantas ações compro?", "tenho US$ X", "quero arriscar no máximo Y" | `trader-budget` |
| "esse trade é seguro?", "passa no meu risco?", "quanto já arrisquei hoje?" | `trader-risk` (sempre antes da corretora) |
| "manda a ordem", "compra", "zera a posição", "conecta a Alpaca" | `trader-broker` (nunca sem o "sim" explícito do usuário) |
| "me avisa se bater o preço", "o mercado está aberto?", "cotação de X" | `trader-alerts` |
| "como foi o balanço?", "o que mudou no trimestre?", "10-Q" | `trader-analyst` |
| "o que a Pelosi comprou?", "trades do Congresso" | `trader-pelosi` |
| "isso é estatisticamente significativo?", "é sorte ou é real?", "quantos trades preciso para saber?" | `statistical-analyst` |
| "esses dados estão certos?", "tem buraco/outlier nas cotações?" | `data-quality-auditor` |

Regras de segurança: nenhuma ordem vai para a corretora sem passar pelo `trader-risk` e sem o "sim" do usuário. Conta paper (simulada) é o padrão.

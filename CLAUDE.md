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

## Fonte de dados: IBKR primeiro

A conta real do usuário é na Interactive Brokers (IBKR) e **toda a operação usa dados de lá**. Os scripts Python não falam com a IBKR (o conector é uma ferramenta do Claude, não uma URL). Então o Claude lê a IBKR pelo conector e entrega os números aos scripts com `.claude/skills/trader-alerts/ibkr_chart.py`:

1. **Achar o contrato:** `search_contracts` com o ticker. Use a linha com `symbol` exato e `country_code` "US"; o número é `underlying_contract_id` (ex.: AAPL = 265598).
2. **Cotação do momento** (alerts, budget, risk): `get_price_snapshot` com `market_data_names` `["last","bid_ask","prior_close","volume","top_status"]`. As chaves da resposta usam hífen (`bid-ask`, `top-status`). Grave com:
   `python3 .claude/skills/trader-alerts/ibkr_chart.py price --symbol AAPL --price <last.price> --time <last.ts> --status <top-status.status>`
   Isso escreve `trader/prices.json` com `"source": "ibkr"`. Diga ao usuário se veio REALTIME ou DELAYED.
3. **Barras** (entry, exit, scanner orb, timemachine): `get_price_history` com `contract_id`, `security_type: "STK"`, `step: "FIVE_MINS"`, `outside_rth: false` e `period` (ex.: `"ONE_DAY"`; `"THREE_MONTHS"` para o timemachine) **ou** `step_count`, nunca os dois. A resposta vem em colunas (`time`, `open`, `high`, `low`, `close`, `volume`, horários ISO do início de cada barra, `chart_step`, `chart_end`). Salve a resposta como veio num arquivo e converta:
   `python3 .claude/skills/trader-alerts/ibkr_chart.py bars --in <arquivo> --symbol AAPL --out trader/ibkr`
   (`--prev-close` é opcional). Depois rode os scripts com `--offline trader/ibkr/<SYM>.json` (entry, exit, scanner orb) ou `--offline trader/ibkr` (timemachine). As mensagens dos scripts ainda dizem "Yahoo" no cabeçalho: o dado é da IBKR, diga isso ao usuário.
4. **O scan amplo** (`scan.py universe` e `scan.py daily`, ~5.900 ações) continua no Yahoo, porque pedir milhares de gráficos ao conector um por um não é viável. Os candidatos finais são sempre confirmados com barras e cotação da IBKR antes de entry/exit/budget/risk.
5. **Reserva:** só quando o conector não estiver na sessão ou falhar (já respondeu "Service Unavailable" algumas vezes). Use o Yahoo e diga, em uma linha, que o dado veio da reserva e por quê. Nunca misture fontes sem avisar e nunca invente um preço.

**Conta e posições** vêm da IBKR também (somente leitura): `get_account_summary`, `get_account_balances`, `get_account_positions`, `get_account_orders`, `get_account_trades`. Use-as para "quanto tenho", "o que está aberto" e "o que já operei hoje" (trader-risk e trader-broker).

**Limites pelo saldo** (`"budget_source": "ibkr"` em `trader/config.json`): o orçamento é sempre o saldo da conta, nunca o número fixo `"budget"`. Antes de cada `trader-budget` ou `trader-risk` (e antes do filtro de preço do scanner):

1. `get_account_summary` (use `net_liquidation` e `total_cash_value`), `get_account_positions`, `get_account_orders` e `get_account_trades` com `period: "DAYS_7"`. Salve as três últimas respostas como vieram, cada uma num arquivo.
2. Grave: `python3 .claude/skills/trader-alerts/ibkr_chart.py account --net-liq <net_liquidation> --cash <total_cash_value> --positions <posições> --orders <ordens> --trades <trades>`. Isso escreve `trader/account.json` (fora do git: o saldo não vai para o repositório público) com os trades de hoje (entradas e resultado realizado) e o stop de cada posição. `--position SYM=QTY@CUSTO_MEDIO` ainda funciona se a resposta de posições faltar.
3. `size.py` e `check.py` leem esse arquivo e calculam os limites em dólar pelos percentuais de `limits_pct`: perda máxima por trade 7,5% do saldo, perda máxima no dia 22,5%, ordem máxima 80%. A posição máxima (`max_position_pct`) e a reserva de caixa (`cash_buffer_pct`) já eram percentuais. O filtro de preço do scanner passa a ser saldo × `max_position_pct` / 100.
4. Se o arquivo faltar ou não for de hoje (Nova York), os scripts **recusam**: leia o saldo de novo. Nunca invente um saldo nem volte para o `"budget"` fixo sem avisar.
5. O `check.py` também recusa sem os trades de hoje (`--trades`): a perda do dia e o número de trades contam o que a IBKR diz que aconteceu, não só o registro do `trader-broker`.

**Vigia do stop** (todo Radar, todo Zerar posições e antes de cada ordem): o `ibkr_chart.py account --orders` imprime `STOP GUARD` com uma linha por posição aberta: `OK`, `PARTIAL` (o stop cobre menos ações que a posição) ou `NO STOP`. Se aparecer `ALERT`, essa é a **primeira linha** da mensagem ao usuário, em destaque (ex.: "TOST: 9 ações sem stop na corretora"), e o `check.py` bloqueia entradas novas até o stop estar lá. Ler ordens é só leitura; quem anexa o stop é o Cristian, no app. No Zerar posições, se houver posição aberta, diga ações, preço atual e o resultado se zerar agora; a venda só com o "sim" dele.

**Caixa preso:** se houver posição aberta e o caixa livre (depois da reserva) não comprar ações suficientes para um plano positivo no alvo depois da comissão, diga isso na primeira linha do Radar ("caixa livre US$ X: trades novos limitados") e mostre só os planos que ficam positivos no alvo.

**Ordens:** `create_order_instruction` e qualquer ferramenta que mude a conta (`delete_order_instruction`, alertas, watchlists e screeners salvos) só com pedido explícito do usuário, depois do `trader-risk` e do "sim" para aquela ordem específica. A conta é **real** (`"paper": false` em `trader/config.json`).

Rotinas: o conector só aparece numa sessão nova e precisa estar na lista de conectores de cada rotina.

## Ciclo de aprendizado (diário de operações)

Toda decisão parte do que as anteriores ensinaram. O diário fica em `/mnt/project-files/diario/` (pasta compartilhada do projeto, privada; **não** no git, porque o repositório é público e o diário tem dólares e trades reais). Detalhes em `.claude/skills/trader-journal/SKILL.md`. Abaixo, `J` = `python3 .claude/skills/trader-journal/journal.py`.

- **Pré-mercado**, antes do scan: `get_account_trades` com `period: "DAYS_7"`, salve a resposta como veio e rode `$J ibkr-trades --in <arquivo>`. Para os planos de ontem sem resultado (`$J show --date <ontem>`), puxe as barras de 5 min da IBKR (`period: "TWO_DAYS"`), converta com `ibkr_chart.py bars --out trader/ibkr` e rode `$J simulate --date <ontem> --bars trader/ibkr`. Depois, `$J lessons`.
- **Radar**, antes de escolher: `$J lessons`. Aplique as **Regras aprovadas**; as **Observações** são só contexto. Se houver posição aberta de outro dia, diga isso primeiro. Depois do resumo, registre **cada** plano mostrado: `$J plan --symbol X --entry E --stop S --target T --shares N --time <hora do post ET> --signal-time <hora do rompimento> --risk PASS|FAIL --tm-mean-r <R médio> --data ibkr`.
- **Thread de ordem** ("quero operar X"): registre o plano refeito com `$J plan`, a resposta do Cristian com `$J decide` (`operado` depois do "sim"; `recusado` ou `nao_confirmado` se não), e marque a linha do radar com `--superseded-by <id novo>`. Depois da execução, `$J ibkr-trades`.
- **Zerar posições**: `get_account_trades` (`TODAY`) → `$J ibkr-trades`, e diga quais linhas seguem abertas.
- **Revisão semanal** (sexta depois do fechamento ou sábado): `$J review`, poste o relatório e pergunte ao Cristian sobre cada proposta, um "sim" por proposta. Uma regra só entra em `licoes.md` com `$J note --rule --approved-by "<quem, onde, quando>"` depois desse "sim"; se mexer num script ou no `trader/config.json`, vai por PR.
- **Nunca** mude uma regra por conta própria nem por uma faixa marcada como "amostra insuficiente" (menos de `journal.min_sample` = 30 casos). Com pouca amostra, "aprender" é ajustar a estratégia ao acaso (overfitting). Lições de processo (um script que conta errado, um tipo de ordem que o conector não tem) podem ser registradas na hora como observação.
- Se a pasta `/mnt/project-files` não existir na sessão, o script avisa e grava em `trader/diario`, que se perde: avise o Cristian.

## Arquivos gerados

Sessões na nuvem são apagadas ao terminar. Quando uma skill gerar um arquivo que valha guardar (relatório, plano, contexto), salve-o dentro deste repositório e ofereça fazer commit.

## Mapa de pedidos

Ordem do dia: `trader-journal lessons` → `trader-scanner` → `trader-news` → `trader-entry` → `trader-exit` → `trader-timemachine` → `trader-budget` → `trader-risk` → `trader-broker`, com `trader-alerts` vigiando stop, alvo e horário, e `trader-journal` registrando cada plano, decisão e resultado.

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
| "o que aprendemos?", "revisão semanal", "registra o trade", "como estão os resultados?", "estou melhorando?" | `trader-journal` |
| "isso é estatisticamente significativo?", "é sorte ou é real?", "quantos trades preciso para saber?" | `statistical-analyst` |
| "esses dados estão certos?", "tem buraco/outlier nas cotações?" | `data-quality-auditor` |

Regras de segurança: nenhuma ordem vai para a corretora sem passar pelo `trader-risk` e sem o "sim" do usuário. Conta paper (simulada) é o padrão.

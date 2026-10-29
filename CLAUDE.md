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

1. `get_account_summary` (use `net_liquidation` e `total_cash_value`) e `get_account_positions`.
2. Grave: `python3 .claude/skills/trader-alerts/ibkr_chart.py account --net-liq <net_liquidation> --cash <total_cash_value> [--position SYM=QTY@CUSTO_MEDIO ...]`. Isso escreve `trader/account.json` (fora do git: o saldo não vai para o repositório público).
3. `size.py` e `check.py` leem esse arquivo e calculam os limites em dólar pelos percentuais de `limits_pct`: perda máxima por trade 7,5% do saldo, perda máxima no dia 22,5%, ordem máxima 80%. A posição máxima (`max_position_pct`) e a reserva de caixa (`cash_buffer_pct`) já eram percentuais. O filtro de preço do scanner passa a ser saldo × `max_position_pct` / 100.
4. Se o arquivo faltar ou não for de hoje (Nova York), os scripts **recusam**: leia o saldo de novo. Nunca invente um saldo nem volte para o `"budget"` fixo sem avisar.

**Ordens:** `create_order_instruction` e qualquer ferramenta que mude a conta (`delete_order_instruction`, alertas, watchlists e screeners salvos) só com pedido explícito do usuário, depois do `trader-risk` e do "sim" para aquela ordem específica. A conta é **real** (`"paper": false` em `trader/config.json`).

Rotinas: o conector só aparece numa sessão nova e precisa estar na lista de conectores de cada rotina.

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

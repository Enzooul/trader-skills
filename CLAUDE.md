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
   **Screener da IBKR (somado ao Yahoo):** no Radar, rode também `preview_screener` (só leitura; **nunca** `create_saved_screener` sem pedido) com `selector: "STK"`, `locations: ["STK.US.MAJOR"]`, `scanner: "SCAN_changeOpenPerc_DESC"`, `max_results: 50` e os filtros `usdPriceAbove` 5, `usdPriceBelow` (saldo × `max_position_pct` / 100), `avgUsdVolumeAbove` 20000000, `openGapPercAbove` 2, `stVolumeVsAvg5minAbove` 2 (testados em 06/11: 59 ações às 10:22 ET). Salve a resposta e rode `ibkr_chart.py screener --in <arquivo>`: ele escreve `trader/ibkr-screener.txt`. Junte esses nomes à lista do Yahoo no `scan.py orb --watchlist` e diga quantos vieram só da IBKR. Registre no diário os planos que vieram **só** do screener com `--tag ibkr_screener`, para a revisão comparar as duas fontes. A resposta traz só nomes e `contract_id` (sem preços), e o `contract_id` dispensa o `search_contracts`. No teste de 06/11 os valores bateram com cotações REALTIME da IBKR (ordem por variação desde a abertura e gap conferidos em 3 ações).
5. **Reserva:** só quando o conector não estiver na sessão ou falhar (já respondeu "Service Unavailable" algumas vezes). Use o Yahoo e diga, em uma linha, que o dado veio da reserva e por quê. Nunca misture fontes sem avisar e nunca invente um preço.

**Conta e posições** vêm da IBKR também (somente leitura): `get_account_summary`, `get_account_balances`, `get_account_positions`, `get_account_orders`, `get_account_trades`. Use-as para "quanto tenho", "o que está aberto" e "o que já operei hoje" (trader-risk e trader-broker).

**Limites pelo saldo** (`"budget_source": "ibkr"` em `trader/config.json`): o orçamento é sempre o saldo da conta, nunca o número fixo `"budget"`. Antes de cada `trader-budget` ou `trader-risk` (e antes do filtro de preço do scanner):

1. `get_account_summary` (use `net_liquidation` e `total_cash_value`), `get_account_positions`, `get_account_orders` e `get_account_trades` com `period: "DAYS_7"`. Salve as três últimas respostas como vieram, cada uma num arquivo.
2. Grave: `python3 .claude/skills/trader-alerts/ibkr_chart.py account --net-liq <net_liquidation> --cash <total_cash_value> --positions <posições> --orders <ordens> --trades <trades>`. Isso escreve `trader/account.json` (fora do git: o saldo não vai para o repositório público) com os trades de hoje (entradas e resultado realizado) e o stop de cada posição. `--position SYM=QTY@CUSTO_MEDIO` ainda funciona se a resposta de posições faltar.
3. `size.py` e `check.py` leem esse arquivo e calculam os limites em dólar pelos percentuais de `limits_pct`: perda máxima por trade 7,5% do saldo, perda máxima no dia 5% (somando o que já foi perdido e o que está em risco nos stops; até 09/11 era 22,5%), ordem máxima 80%. A posição máxima (`max_position_pct`) e a reserva de caixa (`cash_buffer_pct`) já eram percentuais. O filtro de preço do scanner passa a ser saldo × `max_position_pct` / 100.
4. Se o arquivo faltar ou não for de hoje (Nova York), os scripts **recusam**: leia o saldo de novo. Nunca invente um saldo nem volte para o `"budget"` fixo sem avisar.
5. O `check.py` também recusa sem os trades de hoje (`--trades`): a perda do dia e o número de trades contam o que a IBKR diz que aconteceu, não só o registro do `trader-broker`.

**Vigia do stop** (todo Radar, todo Zerar posições e antes de cada ordem): o `ibkr_chart.py account --orders` imprime `STOP GUARD` com uma linha por posição aberta: `OK`, `PARTIAL` (o stop cobre menos ações que a posição) ou `NO STOP`. Se aparecer `ALERT`, essa é a **primeira linha** da mensagem ao usuário, em destaque (ex.: "TOST: 9 ações sem stop na corretora"), e o `check.py` bloqueia entradas novas até o stop estar lá. Ler ordens é só leitura; quem anexa o stop é o Cristian, no app. No Zerar posições, se houver posição aberta, diga ações, preço atual e o resultado se zerar agora; a venda só com o "sim" dele.

**Caixa preso:** se houver posição aberta e o caixa livre (depois da reserva) não comprar ações suficientes para um plano positivo no alvo depois da comissão, diga isso na primeira linha do Radar ("caixa livre US$ X: trades novos limitados") e mostre só os planos que ficam positivos no alvo.

**Janela da estratégia** (aprovado pelo Cristian em 10/11/2026; **prevalece sobre o texto das rotinas de Radar**, que ainda podem citar a escolha de rompimentos antigos ou US$ 2 de comissão): o rompimento da faixa de abertura (ORB) só gera **plano novo** até `radar.orb_new_plans_until` (11:00 de Nova York) e só se o rompimento tiver no máximo `radar.orb_max_signal_age_min` (90) minutos. Rompimento mais velho é descartado no pré-filtro, nunca recalculado pelo preço atual. Depois das 11:00, o Radar fica em **modo vigia**: pré-voo, STOP GUARD, posições abertas e resultado do dia, sem scanner e sem plano novo (economiza o custo das sessões). Isso não muda os limites de risco: o `check.py` continua aceitando entradas até `no_entries_after`.

**Comissão:** desde 10/11/2026 a conta usa o plano **Tiered** da IBKR (US$ 0,0035/ação, mínimo US$ 0,35 por ordem, mais taxas de bolsa, clearing e regulatórias). `journal.commission_round_trip` = 0,85 é uma **estimativa** (`commission_estimated: true`) para ordens de até ~100 ações. Use esse valor no "líquido no alvo" do Radar, mesmo que o texto da rotina diga "~US$ 2 de comissão" (era o plano Fixed). Depois de 3 operações completas no Tiered, compare com as comissões reais do `get_account_trades` e proponha o valor medido por PR.

**Alertas de rompimento** (aprovado pelo Cristian em 10/11/2026): a rotina "Alertas de rompimento" (9:46 NY) pode criar **alertas de preço** na IBKR (`create_alert`, `LAST` `GTE` na máxima da faixa de abertura, `tif` `UNTIL_DATE` = hoje, até 8 por dia). É a única exceção à regra abaixo: nunca ordens, nunca apagar ou mudar alertas que não criou. O alerta é só um aviso; qualquer ordem segue o caminho normal (Radar ou thread de ordem, `trader-risk` e o "sim").

**Disjuntores** (aprovado pelo Cristian em 10/11/2026): o `check.py` bloqueia entradas novas depois de `max_consecutive_losses` (2) saídas com perda seguidas no dia e bloqueia o mesmo ativo por `loss_cooldown_min` (60) minutos depois de uma saída com perda. Conta pelas execuções da IBKR no `account.json`. Se o Radar mostrar `CIRCUIT BREAKER`, essa é a primeira linha da mensagem e nenhum plano novo sai até o dia seguinte.

**Bilhete bracket** (aprovado pelo Cristian em 10/11/2026): para cada plano que o Radar mostrar, inclua uma linha pronta para copiar no app, sempre como **ordem bracket** (compra, stop e alvo enviados juntos, para nunca existir posição sem stop): `BRACKET <TICKER>: comprar <N> a <entrada> (limite) · stop <stop> · alvo <alvo> · validade: dia`. Os números são os do `size.py` e do `check.py`. Se o `check.py` deu BLOCK, não mostre o bilhete.

**Ordens:** `create_order_instruction` e qualquer ferramenta que mude a conta (`delete_order_instruction`, alertas, watchlists e screeners salvos) só com pedido explícito do usuário, depois do `trader-risk` e do "sim" para aquela ordem específica. A conta é **real** (`"paper": false` em `trader/config.json`).

Rotinas: o conector só aparece numa sessão nova e precisa estar na lista de conectores de cada rotina.

## Ciclo de aprendizado (diário de operações)

Toda decisão parte do que as anteriores ensinaram. O diário fica em `/mnt/project-files/diario/` (pasta compartilhada do projeto, privada; **não** no git, porque o repositório é público e o diário tem dólares e trades reais). Detalhes em `.claude/skills/trader-journal/SKILL.md`. Abaixo, `J` = `python3 .claude/skills/trader-journal/journal.py`.

- **Pré-voo** (primeiro passo do Pré-mercado, de cada Radar e do Zerar posições): teste a IBKR com um `get_price_snapshot` e um `get_price_history` da AAPL (contrato 265598), grave o `account.json` (seção "Limites pelo saldo") e rode `python3 .claude/skills/trader-alerts/preflight.py --quote ok|fail --bars ok|fail --stage premarket|radar|flat`. **VERMELHO** é a primeira linha da mensagem e nenhum plano novo sai até resolver; **AMARELO** é dito em uma linha (ex.: barras do Yahoo). O resultado também vale como observação no diário quando for vermelho.
- **Balanço ou notícia forte no dia:** registre o plano com `--tag balanco` (o `trader-news` e o `trader-analyst` dizem se a ação tem balanço hoje ou ontem depois do fechamento). Assim a revisão separa esses dias dos comuns.
- **Pré-mercado**, antes do scan: `get_account_trades` com `period: "DAYS_7"`, salve a resposta como veio e rode `$J ibkr-trades --in <arquivo>`. Para os planos de ontem sem resultado (`$J show --date <ontem>`), puxe as barras de 5 min da IBKR (`period: "TWO_DAYS"`; o `simulate` lista o contrato de cada ação, então não precisa de `search_contracts`, e o conector aceita ~30 pedidos por minuto), converta com `ibkr_chart.py bars --out trader/ibkr` e rode `$J simulate --date <ontem> --bars trader/ibkr`. Depois, `$J lessons`.
- **Radar**, antes de escolher: `$J lessons`. Aplique as **Regras aprovadas**; as **Observações** são só contexto. Se houver posição aberta de outro dia, diga isso primeiro. Depois do resumo, registre **cada** plano mostrado: `$J plan --symbol X --entry E --stop S --target T --shares N --time <hora do post ET> --signal-time <hora do rompimento> --risk PASS|FAIL --tm-mean-r <R médio> --data ibkr --contract-id <contrato IBKR> --recomendado sim|nao --motivo "<regra que decidiu>"`. A etiqueta fica congelada na hora do aviso (com a versão das regras e um hash): é ela que a revisão usa para saber se o "recomendado" funciona.
- **Thread de ordem** ("quero operar X"): registre o plano refeito com `$J plan`, a resposta do Cristian com `$J decide` (`operado` depois do "sim", com `--excecao "<motivo dele>"` se o plano for não recomendado; `recusado` ou `nao_confirmado` se não), e marque a linha do radar com `--superseded-by <id novo>`. Depois da execução, `$J ibkr-trades`.
- **Zerar posições**: `get_account_trades` (`TODAY`) → `$J ibkr-trades`, e diga quais linhas seguem abertas.
- **Compra fora do sistema:** se o `$J ibkr-trades` imprimir `FORA DO SISTEMA` (compra feita direto no app, sem o "sim" registrado num plano), essa é a primeira linha da mensagem, junto com o STOP GUARD daquela posição.
- **Revisão semanal** (sexta depois do fechamento ou sábado): `$J review`, poste o relatório (ele já traz "A máquina do tempo prevê o resultado?", com o critério combinado em 06/11: com 30 casos de cada lado, se o lado R médio > 0 não for melhor com p < 0,05, propor tirar a máquina do tempo da regra) e pergunte ao Cristian sobre cada proposta, um "sim" por proposta. Uma regra só entra em `licoes.md` com `$J note --rule --approved-by "<quem, onde, quando>"` depois desse "sim"; se mexer num script ou no `trader/config.json`, vai por PR.
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

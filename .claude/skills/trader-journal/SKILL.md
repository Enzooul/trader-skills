---
name: trader-journal
description: >-
  Trading journal and learning loop. Records every plan the routines show
  (Pre-mercado, Radar), the decision on it (operated, declined, never
  confirmed, ignored), the real IBKR fills and P&L when it was traded, and a
  simulated result on 5-minute bars when it was not. Prints the short
  briefing the routines read before deciding (approved rules, recent
  observations, open positions, results by bucket) and writes a weekly review
  that proposes rule changes only with enough sample, never applying them.
  Use when the user says "diario", "o que aprendemos", "revisao semanal",
  "registra o trade", "como estao os resultados", "estou melhorando?", or at
  the start of every Pre-mercado and Radar run.
argument-hint: "[lessons | plan | decide | ibkr-trades | simulate | review | stats | show]"
---

# trader-journal

The other skills look at one day. The journal remembers all of them, so the
next decision can start from what the last ones taught. It does three things:

1. **Remember.** One row per plan: what was planned (entry, stop, target,
   shares, R:R, time machine), what was decided and why, and what happened.
   A plan that was not traded still gets a result, simulated on the day's
   5-minute bars, so a "no" teaches as much as a "yes".
2. **Brief.** `lessons` prints, in about 30 lines, what a routine must know
   before it picks a trade: the approved rules, the recent observations, any
   position still open, and the results so far by bucket, with the buckets
   too small to trust marked.
3. **Review.** Once a week `review` writes a report and proposes a rule change
   only when a bucket has at least `min_sample` (30) cases on each side of the
   comparison and the difference survives a Bonferroni correction. Otherwise
   it says "nenhuma" and why. **It never changes a rule.** A proposal becomes a
   rule only after Cristian's explicit "sim"; then it is written to
   `licoes.md` (`note --rule --approved-by ...`) and, if it changes a script
   or `trader/config.json`, it goes in a pull request.

Why so strict: with a few dozen trades, any split of the data finds a bucket
that "worked" by luck. Adjusting the strategy to it is overfitting, and the
backtests of October already showed that rules that won in one period lost in
the next. Process lessons (a script that miscounts, an order type the
connector lacks) do not depend on luck and can be fixed right away, with
Cristian's approval.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

## Where the journal lives

`/mnt/project-files/diario/` (the project's shared folder), **not** the git
repository. The repository is public, and the journal holds real dollars and
the account's trades (CLAUDE.md already keeps the balance out of git). The
shared folder is private to the project, outlives every session, and every
thread of the project can read and write it, including the routine runs.

| file | what |
| --- | --- |
| `diario.jsonl` | one JSON row per plan (see below) |
| `licoes.md` | `## Regras aprovadas` (applied by the routines) and `## Observacoes` (context only) |
| `revisoes/revisao-YYYY-Www.md` | one weekly review |
| `backfill/` | the one-off import of 22/10 to 03/11 and its bars |

Override with `--dir`, `TRADER_JOURNAL_DIR`, or `"journal": {"dir": ...}` in
`trader/config.json`. If the folder is missing (a session outside the
project), the script says so and writes to `trader/diario`, which is lost when
the session ends.

## Commands

```bash
J="python3 ${CLAUDE_SKILL_DIR}/journal.py"
$J lessons                                   # the briefing, before any decision
$J plan --symbol AAPL --entry 230.10 --stop 229.40 --target 231.50 --shares 4 \
        --time 10:25 --signal-time 10:15 --risk PASS --tm-mean-r 0.05 --data ibkr \
        --recomendado nao --motivo "R medio <= 0"
$J decide --date 2026-11-04 --symbol AAPL --status recusado --reason "comissao come o ganho"
$J decide --id 2026-11-04-AAPL-2 --status operado --reason "Cristian: sim" --excecao "quis assim mesmo"
$J verify                                    # no frozen label was edited after it was written
$J ibkr-trades --in trader/ibkr/trades.json  # raw get_account_trades answer, saved as-is
$J simulate --date 2026-11-04 --bars trader/ibkr   # after the close, bars from ibkr_chart.py bars
$J review                                    # this week from Friday on, else last week
$J stats --by atraso --by risco_acao         # the numbers behind the briefing
$J show --days 3                             # the rows of the last 3 days
$J note --kind processo --text "..."         # an observation
$J note --rule --approved-by "Cristian, thread X, 04/11" --text "..."   # only after his "sim"
```

- `plan` merges a plan shown again by a later radar (same day, ticker, entry
  and stop) into the same row and adds the time to `seen_at`. A plan re-made
  at order time (new entry or target) is a new row; mark the radar row with
  `decide --superseded-by <new id>` so the same breakout counts once.
- `ibkr-trades` matches each fill to the plan of that day and ticker (the one
  marked `operado` first, then the latest shown before the fill), groups fills
  until the position is back to zero, and computes the real result with IBKR's
  own prices and commissions. Fills already in the journal are skipped, so it
  is safe to import `DAYS_7` every day. A fill with no plan becomes a `manual`
  row. A position still open after the session is flagged in `lessons`.
- `simulate` uses one rule for every day, so days compare: market order at
  the open of the first 5-minute bar after the plan was posted; skip if that
  price is already under the stop, over the target, or gives R:R under 1;
  stop first when one bar touches both (conservative); flat at the close of
  the 15:50 bar. Commission `journal.commission_round_trip` (US$ 2, IBKR Pro
  Fixed). It reads `<SYM>.json` (the Yahoo v8 format `ibkr_chart.py bars`
  writes) and only the bars of `--date` in New York.
- `plan --recomendado sim|nao --motivo "<regra>"` freezes the label the
  routine gave when it SHOWED the plan: the label, the reason, the approved
  rule set in force (`rules`: count, newest date and a fingerprint of the
  "Regras aprovadas" section), the plan and the time, with a hash. A later
  `plan` on the same row never changes it (a new label needs a new plan row),
  and `verify` / `review` list any label whose hash no longer matches. Only
  frozen labels enter the review's "recomendado x nao recomendado" table, so
  the filter is judged on what was said before the outcome was known.
- `decide --status operado` on a plan labelled `nao` records an `exception`
  (`--excecao "<motivo>"`, asked of Cristian before the order). The review
  shows the exceptions apart.
- `ibkr-trades` marks a buy as `off_system` ("fora do sistema") when no
  plan of that day and ticker had a recorded "sim" (`decide ... operado`)
  before the import: it was bought straight in the app and never went
  through trader-risk. The command prints `FORA DO SISTEMA` for it: say it
  first. Its decision gets `auto: true`.
- `plan --contract-id` keeps the IBKR contract; `simulate` then lists the
  contracts of the plans whose bars are missing, so the Pre-market asks
  `get_price_history` directly (no `search_contracts`, which the connector's
  ~30 requests per minute made the bottleneck on 09/11).
- `review` also prints the falsification checks of the 09/11 audit: a 95%
  interval that resamples whole days, the result without the best trade,
  with the cost doubled and the share of the top 3 trades, for all signals,
  frozen-label recommended / not recommended and real trades; and whether
  "recomendado" has met the proposed criterion (100 frozen labels, interval
  above zero, survives the checks, beats the other groups). Both only
  measure: nothing blocks a trade.
- `review` has a table of every real trade: through the system or not,
  breakout / post / "sim" / fill times, the delay from breakout to fill, and
  the slippage (price paid minus the plan's entry, in dollars and in R).
- `plan --tag balanco` marks a plan on an earnings (or strong news) day; the
  `balanco` bucket keeps those days apart from ordinary ones.
- `review` also answers "does the time machine predict the result?": the
  results of plans with time-machine mean R <= 0 against > 0, and the rank
  correlation. Pre-registered on 2026-11-06: with `min_sample` results on each
  side, if the R > 0 side is not better with p < 0.05, the review proposes
  taking the time machine out of the "R medio > 0" rule. Before that it only
  counts how many cases are missing.
- **R** is the result in multiples of the planned risk, before commission:
  +1 R won what it risked, -1 R hit the stop. It compares trades of any size.
  The dollar net shows what the commission did.

## Row format (`diario.jsonl`)

```json
{"id": "2026-10-30-PTEN-2", "date": "2026-10-30", "symbol": "PTEN", "source": "radar",
 "first_seen": "11:29", "seen_at": ["11:29"], "signal_time": "10:40", "setup": "orb",
 "plan": {"entry": 13.10, "stop": 12.80, "target": 13.70, "shares": 21, "rr": 2.0, "risk_per_share": 0.3},
 "risk_pass": "PASS", "tm_mean_R": 0.1, "data": "ibkr", "tags": ["earnings"],
 "decision": {"status": "operado", "reason": "...", "at": "..."},
 "real": {"status": "fechado", "fills": [...], "entry_price": 13.095, "exit_price": 13.305,
          "commission": 2.01, "gross": 4.41, "net": 2.4, "outcome": "zerou", "R": 0.71},
 "sim": {"fill_time": "11:30", "fill_price": 13.08, "exit_time": "15:55", "outcome": "zerou 15:55",
         "R": 0.77, "net": 2.52, "bars": "yahoo", "method": "..."}}
```

`status` is one of `planejado`, `operado`, `recusado` (Cristian or the risk
check said no), `nao_confirmado` (an order was shown and no "sim" came),
`ignorado` (shown, nobody acted). The learning uses the real result when the
trade is closed, else the simulation.

## Steps in the day

1. **Pre-mercado** (before the scan): read the IBKR trades (`DAYS_7`) and
   import them; simulate yesterday's plans that have no result (bars from
   IBKR, `period: "TWO_DAYS"`, converted with `ibkr_chart.py bars`); then
   `lessons`. Mention the approved rules that apply today and any open
   position (a position from another day breaks the flat-by rule: say so first).
2. **Radar** (before choosing): `lessons`. Apply the approved rules. Then, for
   every plan in the summary, `plan` with the post time, the breakout time,
   the risk verdict, the time machine mean R and the label the summary gave
   (`--recomendado sim|nao --motivo`).
3. **Order thread** (when Cristian says "quero operar X"): `plan` for the
   re-made plan (with its own frozen label), `decide` with his answer
   (`operado` after his "sim", with `--excecao "<motivo>"` when the plan is
   not recommended; `recusado` or `nao_confirmado` otherwise),
   `--superseded-by` on the radar row. After the fill, `ibkr-trades`.
4. **Zerar posicoes**: import today's IBKR trades and say which journal rows
   are still open.
5. **Weekly review** (Friday after the close, or Saturday): `review`, post the
   report, and ask Cristian about each proposal, one "sim" per proposal.

## Rules

- Never invent a row, a fill or a result. A plan without a known time or
  price stays out, with a note.
- Say "simulado" for every simulated number and "real" only for IBKR fills.
- A bucket marked `*` or "(pouco)" is not evidence. Never change a rule, a
  ranking or a size because of it.
- The journal never sends, sizes or blocks an order. trader-risk and the
  per-order "sim" still decide.

---
name: trader-backtest
description: >-
  Backtester. Replays the whole day-trading routine on real past bars before
  any money is risked: trader-scanner's daily list, the affordable filter, the
  opening-range breakout, trader-exit's stop and target, trader-budget's size
  and trader-risk's limits from trader/config.json, out by flat-by. Free Yahoo
  data kept in a local store that grows past Yahoo's 60-day limit when it is
  fetched every week or two. Says the win rate, loss rate, average R, dollars
  per trade with a confidence range, net result and max drawdown, under four
  cost levels, and checks rule variants on sessions they were not chosen on.
  Use when the user says "backtest", "does this strategy make money", "test
  the routine on past data", "win rate", "what if I change the stop/target",
  "is it viable", "validate the strategy", or before going from paper to live.
argument-hint: "[fetch | run | signals | grid] [--variant current|sip30|zarattini]"
---

# trader-backtest

The Backtester asks one question before anything else in this pack is trusted
with money: if the routine had run on the last N sessions, exactly as it is
configured, what would have happened? It replays every session bar by bar with
only what was known at that moment. A good backtest is not a promise, and a bad
one is a warning worth listening to. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/backtest.py" fetch                      # build / extend trader/backtest/store (~25 min the first time)
python3 "${CLAUDE_SKILL_DIR}/backtest.py" run                        # the current rule, hourly-routine entries, medium costs
python3 "${CLAUDE_SKILL_DIR}/backtest.py" run --mode ideal --cost zero
python3 "${CLAUDE_SKILL_DIR}/backtest.py" signals --variant sip30    # every signal in R, first 60 % vs last 40 % of the sessions
python3 "${CLAUDE_SKILL_DIR}/backtest.py" grid                       # 1,152 variants, chosen on the first part, checked on the rest
python3 "${CLAUDE_SKILL_DIR}/backtest.py" today                      # PAPER experiment: sip30 on today's daily list, after 10:05 ET
```

Flags: `--variant current|sip30|zarattini`, `--cost zero|baixo|medio|alto`,
`--mode routine|ideal`, `--delay 10` (minutes from the routine's run to the
user's order), `--skip 6` (first sessions left out), `--split 0.6`, `--json`.
`fetch` takes `--symbols A,B`, `--threads 6`, `--max-price 160`. `today`
takes `--variant sip30`, `--watchlist trader/watchlist.json` (or
trader/affordable.txt) and `--now "2026-10-21 14:20"` to replay a past session.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | from `scan.py universe`; `fetch` reads it |
| `trader/config.json` | the limits: budget, cash buffer, position cap, max order, max loss per trade and per day, trades per day, R:R, entry window, flat-by |
| `trader/backtest/store/<SYM>.json.gz` | daily bars (1 year) and 5-minute bars, merged on every fetch |
| Yahoo chart and spark API | free and unofficial; 5-minute bars only for the last 60 days |

## The variants

| name | rule |
| --- | --- |
| `current` | the routine today: trader-scanner daily list, 15-minute range, first close above it, stop at the range midpoint, 2R target |
| `sip30` | the best area of the 2026-10-22 grid: the 20 names of the daily list with the highest opening volume against their own last 14 days, 30-minute range, buy-stop 1 cent above it, only while SPY is up on the day, stop 10 % of the 14-day ATR, no target, out at flat-by. Near break-even after costs, NOT a proven edge |
| `zarattini` | the long side of Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy For The U.S. Equity Market": 5-minute range on the 20 "stocks in play", buy-stop, first candle up, 10 % ATR stop, out at the close |

## Result on 2026-10-22 (60 sessions, 2026-07-29 to 2026-10-21)

- `current`, 11,043 signals: average R -0.08 with no costs, -0.24 with medium
  costs. Negative in every setup, month and nearly every hour.
- On the $200 config: 31 % to 40 % of trades won, 14 % to 17 % reached 2R,
  net -$27 to +$38 over 60 sessions depending on costs and entry delay, none
  distinguishable from zero.
- The trader-timemachine verdict did not predict outcomes (1,838 signals).
- Grid: the best variants on the first 34 sessions lost on the next 20; the
  one that stayed positive (`sip30`) made its money on 3 days.

## The paper experiment (`today`)

`today` runs `sip30` on the session so far: it needs `scan.py daily` first
(trader/watchlist.json), fetches 3 months of daily bars and 60 days of
5-minute bars for each name (about a minute for 600 names), ranks the daily
list by opening volume against each name's last 14 sessions, and prints for
the 20 most active: TRIGGERED (time, buy-stop fill, stop at 10 % of the ATR,
whole shares from the config) or waiting (the buy-stop level and its stop),
and whether SPY was up when it fired (the rule skips it if not). The opening
range is 30 minutes, so nothing fires before 10:00 ET. It is for paper
trading only: the rule took the first 3 that passed in the backtest, has no
target, and is out by flat-by. Its stops are tight (often 5 to 20 cents), so a
spread of a cent or two is a big part of the risk. Say all that, and that it
is an experiment, every time. Record each paper trade so it can be compared
with the backtest after a few weeks.

## Steps

1. If `trader/backtest/store` is empty or older than two weeks: `fetch`.
2. `run` with the user's config. Tell them, in this order: sessions and dates,
   trades, won / lost, target / stop / flat-by, average R, dollars per trade
   with its 95 % range, net, max drawdown. Then the same with `--cost alto`.
3. `signals` for the big-sample answer: is the average R above zero, after
   costs, on BOTH parts of the sessions? If not, say plainly that the rule
   has no edge on this data.
4. For a change to the rule, `grid` or `signals --variant`. A variant counts
   only if it holds on the sessions it was not chosen on, with costs, and not
   because of two or three days.
5. Remind the user what the numbers leave out: exchange spread, IOF, wire and
   withdrawal fees, income tax, and that past bars are not a prediction.

## Rules

- Never call a variant profitable from the first part alone. Many tries make
  some look good by luck: say how many were tried.
- Every number comes from a run. Say the cost level and the dates with it.
- Long side only, like the rest of the pack.
- It never places an order and never edits trader/config.json.

## Safety

- Education and tooling, not financial advice. A positive backtest is not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".

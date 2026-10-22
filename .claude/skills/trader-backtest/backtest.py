#!/usr/bin/env python3
"""Backtest for trader-backtest. Standard library only.

Replays the whole routine (trader-scanner daily -> affordable list -> opening-range
breakout -> trader-exit stop and target -> trader-budget size -> trader-risk limits ->
flat by 15:55) on real past Yahoo bars, with no look-ahead, and says how it did.

    python3 backtest.py fetch                       # download / extend the data store (~25 min the first time)
    python3 backtest.py fetch --symbols TSLA,AMD    # a few names only
    python3 backtest.py run                         # the current rule on your config, hourly-routine entries
    python3 backtest.py run --mode ideal --cost zero
    python3 backtest.py run --variant sip30
    python3 backtest.py signals --variant current   # every signal, in R, first 60 % vs last 40 % of the days
    python3 backtest.py grid                        # many variants, chosen on the first 60 %, checked on the rest

The data store (trader/backtest/store/<SYM>.json.gz) keeps daily bars (1 year)
and 5-minute bars. Yahoo serves only the last 60 days of 5-minute bars, so
`fetch` MERGES new bars into what is already stored: run it every week or two
and the history grows past 60 days. A gap longer than 60 trading days between
fetches leaves a hole that cannot be filled from Yahoo.

How a session D is replayed:
  1. Daily scan as of D-1 (scan.find_setups on closes up to D-1; price >= 5,
     D-1 volume >= 1,000,000), then the routine's price filter: price <=
     budget x max_position_pct / 100.
  2. Universe of the variant: that list ("hits"), the 20 names with the highest
     relative volume in the opening range among liquid names ("rv20": price 5
     to the cap, 14-day average volume >= 1M, 14-day ATR >= $0.50, relative
     volume >= 1), or the 20 highest of the daily list ("rv20hits").
  3. Entry: the first completed bar after the opening range that CLOSES above
     its high (entry "close", the trader-entry rule; fill at that close), or a
     buy-stop 1 cent above the range high (entry "stop"; fill at the stop
     price, or the bar's open on a gap). Only between no_entries_before and
     no_entries_after.
  4. Stop: or_mid (trader-exit default), or_low, or atrK = K % of the 14-day
     daily ATR under the fill. Target: tgt x R, or 0 = none (out at flat_by).
  5. Size: whole shares, the smallest of max_loss_per_trade / risk, the
     position cap, max_order_usd and the free cash (budget less the buffer,
     less open positions). Risk is measured from the higher of the plan's entry
     and the fill (trader-risk). Refused: R:R under min_reward_risk, the day's
     summed max losses over max_daily_loss, max_trades_per_day reached.
  6. Exit bar by bar: a bar that touches stop and target counts as the stop;
     an open below the stop fills at the open; otherwise out at the flat_by bar.
     For a buy-stop fill inside a bar, only that bar's close is checked
     against the stop (the order of high and low inside a bar is unknown).

Modes: "ideal" enters at the trigger, as if someone watched every bar.
"routine" enters as the hourly cloud routine allows: it runs at 10:15, 11:15
... 15:15 ET, takes the most recent breakouts first (then the most traded),
and the user acts --delay minutes later at that bar's open.

Costs a side (spread + slippage), --cost: zero; baixo = $0.01 a share;
medio = max($0.01, 0.05 %); alto = max($0.02, 0.10 %). The stop and the
flat-by exit pay it; the target is a resting limit (no cost) that, with any
cost, fills only when the price trades 1 cent through it. Alpaca's
regulatory fees (SEC on sells, CAT both sides, rounded up per day) are added.

This is a replay of rules on past bars, not a prediction. Free Yahoo data is
unofficial; 5-minute bars, never tick by tick; the list of stocks is today's,
so names that stopped trading are missing (survivorship). Nothing here places
an order.
"""

import argparse
import concurrent.futures
import datetime
import gzip
import itertools
import json
import math
import os
import random
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "trader-scanner"))
sys.path.insert(0, os.path.join(HERE, "..", "trader-timemachine"))
import scan  # noqa: E402
import timemachine as tm  # noqa: E402

STORE = "trader/backtest/store"
CONFIG = "trader/config.json"
UNIVERSE = "trader/universe.txt"
OPEN_MIN = 9 * 60 + 30
COSTS = {"zero": (0.0, 0.0), "baixo": (0.01, 0.0), "medio": (0.01, 0.0005), "alto": (0.02, 0.001)}
VARIANTS = {
    # the routine today: trader-scanner daily list, 15-minute range, close above, or-mid stop, 2R
    "current": {"univ": "hits", "n": 3, "entry": "close", "bull": False, "stop": "or_mid", "tgt": 2, "mkt": False},
    # the best area of the 2026-10-22 grid (near break-even with costs, NOT a proven edge)
    "sip30": {"univ": "rv20hits", "n": 6, "entry": "stop", "bull": False, "stop": "atr10", "tgt": 0, "mkt": True},
    # Zarattini, Barbon & Aziz (2024) 5-minute ORB on stocks in play, long side only
    "zarattini": {"univ": "rv20", "n": 1, "entry": "stop", "bull": True, "stop": "atr10", "tgt": 0, "mkt": False},
}
GRID = {
    "univ": ["hits", "rv20", "rv20hits"], "n": [1, 3, 6], "entry": ["close", "stop"], "bull": [False, True],
    "stop": ["or_mid", "or_low", "atr10", "atr25"], "tgt": [1, 2, 3, 0], "mkt": [False, True],
}


class BTError(Exception):
    pass


# ---------------------------------------------------------------- the store

def store_path(sym):
    return os.path.join(STORE, sym + ".json.gz")


def read_store(sym):
    p = store_path(sym)
    if not os.path.exists(p):
        return {"d1": {}, "m5": {}}
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_store(sym, doc):
    os.makedirs(STORE, exist_ok=True)
    tmp = store_path(sym) + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, store_path(sym))


def merge_chart(doc, text, kind):
    """Merge a Yahoo v8 chart answer into the store doc. d1: {date: [h, l, c, v]};
    m5: {date: {slot: [o, h, l, c, v]}} regular-session complete bars only."""
    try:
        res = json.loads(text)["chart"]["result"][0]
    except (ValueError, KeyError, TypeError, IndexError):
        return 0
    if kind == "d1":
        q = res["indicators"]["quote"][0]
        n = 0
        for i, ts in enumerate(res.get("timestamp") or []):
            h, lo, c, v = q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
            if None in (h, lo, c):
                continue
            doc["d1"][scan.ts_to_eastern(ts).date().isoformat()] = [h, lo, c, v or 0]
            n += 1
        return n
    _, bars, _ = tm.parse_bars(res, scan.parse_now(None))
    for b in bars:
        doc["m5"].setdefault(b["date"], {})[str(b["slot"])] = [b["o"], b["h"], b["l"], b["c"], b["v"]]
    return len(bars)


def cmd_fetch(a):
    t0 = time.time()
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        if not os.path.exists(UNIVERSE):
            raise BTError("no %s: run trader-scanner first (scan.py universe)" % UNIVERSE)
        syms = scan.symbols_from_file(UNIVERSE)
        spark, _ = scan.fetch_spark(syms, "3mo", "1d", 0.2)
        keep = []
        for s, ser in spark.items():
            cs = [c for c in ser["c"] if c is not None]
            if cs and max(cs) >= 4 and min(cs) <= a.max_price:
                keep.append(s)
        print("Universe %s symbols; %s traded between $4 and $%s in the last 3 months."
              % ("{:,}".format(len(syms)), "{:,}".format(len(keep)), a.max_price), flush=True)
        syms = sorted(keep)
    syms = sorted(set(syms) | {"SPY"})

    def job(sym):
        doc = read_store(sym)
        got = []
        for rng, itv, kind in (("1y", "1d", "d1"), ("60d", "5m", "m5")):
            for attempt in range(3):
                try:
                    got.append(merge_chart(doc, scan.yahoo_get(scan.chart_path(sym, rng, itv)), kind))
                    break
                except scan.ScanError as e:
                    err = str(e)
                    time.sleep(2 + 3 * attempt)
            else:
                return sym, "failed: " + err
        write_store(sym, doc)
        return sym, "ok %d daily, %d five-minute sessions" % (len(doc["d1"]), len(doc["m5"]))

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(a.threads) as ex:
        for i, (sym, msg) in enumerate(ex.map(job, syms), 1):
            if not msg.startswith("ok"):
                failed += 1
            if i % 250 == 0:
                print("  %d of %d" % (i, len(syms)), flush=True)
    print("Stored %d symbols in %s (%d failed) in %.0f s. Yahoo keeps 60 days of 5-minute bars: "
          "fetch again within ~50 trading days to keep the history unbroken."
          % (len(syms) - failed, STORE, failed, time.time() - t0))
    return 0


# ---------------------------------------------------------------- loading

def load_store():
    if not os.path.isdir(STORE):
        raise BTError("no data in %s: run `backtest.py fetch` first" % STORE)
    D1, M5 = {}, {}
    for f in sorted(os.listdir(STORE)):
        if not f.endswith(".json.gz"):
            continue
        sym = f[:-8]
        doc = read_store(sym)
        D1[sym] = {datetime.date.fromisoformat(d): tuple(v) for d, v in doc["d1"].items()}
        M5[sym] = {datetime.date.fromisoformat(d): {int(k): {"o": b[0], "h": b[1], "l": b[2], "c": b[3], "v": b[4]}
                                                     for k, b in s.items()} for d, s in doc["m5"].items()}
    spy = M5.pop("SPY", {})
    D1.pop("SPY", None)
    cnt = {}
    for s in M5:
        for d in M5[s]:
            cnt[d] = cnt.get(d, 0) + 1
    if not cnt:
        raise BTError("the store has no 5-minute bars")
    days = sorted(d for d in cnt if cnt[d] > 0.5 * max(cnt.values()))
    return D1, M5, spy, days


def read_config():
    if not os.path.exists(CONFIG):
        raise BTError("no %s: set your limits with trader-budget first" % CONFIG)
    with open(CONFIG, encoding="utf-8") as fh:
        c = json.load(fh)
    hm = lambda t, d: (int(t[:2]) * 60 + int(t[3:])) if t else d  # noqa: E731
    return {
        "budget": float(c["budget"]),
        "cash": float(c["budget"]) * (1 - float(c.get("cash_buffer_pct", 10)) / 100),
        "cap": float(c["budget"]) * float(c.get("max_position_pct", 75)) / 100,
        "max_order": float(c.get("max_order_usd", 1e12)),
        "min_order": float(c.get("min_order_usd", 0)),
        "max_loss": float(c["max_loss_per_trade_usd"]),
        "day_loss": float(c.get("max_daily_loss_usd", 1e12)),
        "trades": int(c.get("max_trades_per_day", 3)),
        "min_rr": float(c.get("min_reward_risk", 1.0)),
        "first": hm(c.get("no_entries_before"), 9 * 60 + 45),
        "last": hm(c.get("no_entries_after"), 15 * 60 + 30),
        "flat_slot": (hm(c.get("flat_by"), 15 * 60 + 55) - OPEN_MIN) // 5 - 1,
    }


# ---------------------------------------------------------------- one session

class Day(object):
    """Everything a session needs, computed once: the daily list and the liquid names."""

    def __init__(self, D1, M5, spy, day, cfg):
        self.day, self.cfg, self.spy = day, cfg, spy.get(day, {})
        self.feats = []
        for s, dd in D1.items():
            bars = M5.get(s, {}).get(day)
            if not bars or 0 not in bars:
                continue
            prev = sorted(d for d in dd if d < day)
            if len(prev) < scan.MIN_DAILY_BARS:
                continue
            h, lo, pc, pv = dd[prev[-1]]
            if not 5 <= pc <= cfg["cap"]:
                continue
            tags = []
            if pv >= 1_000_000:
                tags, _ = scan.find_setups([dd[d][2] for d in prev[-260:]])
            last15 = prev[-15:]
            av = statistics.mean(dd[d][3] for d in last15[1:])
            trs = [max(dd[d][0], dd[p][2]) - min(dd[d][1], dd[p][2]) for p, d in zip(last15[:-1], last15[1:])]
            atr = statistics.mean(trs)
            past = [d for d in sorted(M5[s]) if d < day][-14:]
            rv = {}
            for n in (1, 3, 6):
                if all(i in bars for i in range(n)):
                    hist = [sum(M5[s][d][i]["v"] for i in range(n)) for d in past
                            if all(i in M5[s][d] for i in range(n))]
                    if len(hist) >= 5 and statistics.mean(hist) > 0:
                        rv[n] = sum(bars[i]["v"] for i in range(n)) / statistics.mean(hist)
            self.feats.append({"s": s, "bars": bars, "atr": atr, "tags": tags, "rv": rv,
                               "liquid": av >= 1_000_000 and atr >= 0.5, "dv": pc * pv})

    def universe(self, univ, n):
        if univ == "hits":
            return [f for f in self.feats if f["tags"]]
        pool = [f for f in self.feats if f["liquid"] and f["rv"].get(n, 0) >= 1.0]
        if univ == "rv20hits":
            pool = [f for f in pool if f["tags"]]
        return sorted(pool, key=lambda f: -f["rv"][n])[:20]

    def spy_up(self, slot):
        return 0 in self.spy and slot in self.spy and self.spy[slot]["c"] > self.spy[0]["o"]


def signal(f, v, cfg):
    """-> dict(k, close_min, fill_slot, fill, oh, ol, inside) or None."""
    bars, n = f["bars"], v["n"]
    if not all(i in bars for i in range(n)):
        return None
    oh = max(bars[i]["h"] for i in range(n))
    ol = min(bars[i]["l"] for i in range(n))
    if v["bull"] and not bars[n - 1]["c"] > bars[0]["o"]:
        return None
    for k in range(n, 78):
        b = bars.get(k)
        if b is None:
            return None                        # a missing bar: the scanner drops the chart
        close_min = OPEN_MIN + 5 * (k + 1)
        if v["entry"] == "close":
            if close_min < cfg["first"]:
                continue
            if close_min > cfg["last"]:
                return None
            if b["c"] > oh:
                return {"k": k, "close_min": close_min, "fill_slot": k + 1, "fill": round(b["c"], 2),
                        "oh": oh, "ol": ol, "inside": False}
        else:
            start_min = OPEN_MIN + 5 * k
            if start_min > cfg["last"]:
                return None
            if start_min + 5 <= cfg["first"]:
                continue
            if b["h"] >= oh + 0.01:
                return {"k": k, "close_min": close_min, "fill_slot": k, "fill": round(max(oh + 0.01, b["o"]), 2),
                        "oh": oh, "ol": ol, "inside": True}
    return None


def plan(f, sig, v, entry=None):
    entry = sig["fill"] if entry is None else entry
    if v["stop"] == "or_mid":
        stop = tm.cents_away((sig["oh"] + sig["ol"]) / 2, True)
    elif v["stop"] == "or_low":
        stop = tm.cents_away(sig["ol"], True)
    else:
        stop = tm.cents_away(entry - float(v["stop"][3:]) / 100 * f["atr"], True)
    risk = entry - stop
    if risk <= 0 or risk < entry * 0.0005:
        return None
    target = round(entry + v["tgt"] * risk + 1e-9, 2) if v["tgt"] else None
    return {"entry": entry, "stop": stop, "target": target, "risk": risk}


def cost(px, slip):
    return max(slip[0], slip[1] * px)


def walk(bars, start, stop, target, slip, flat_slot):
    """-> (exit price, exit slot, outcome). Stop before target inside a bar."""
    through = 0.01 if slip[0] or slip[1] else 0.0
    for i in range(start, flat_slot + 1):
        b = bars.get(i)
        if b is None:
            continue
        if b["o"] <= stop:
            return b["o"] - cost(b["o"], slip), i, "stop"
        if b["l"] <= stop:
            return stop - cost(stop, slip), i, "stop"
        if target is not None and b["h"] >= target + through:
            return target, i, "target"
    last = max(k for k in bars if k <= flat_slot)
    return bars[last]["c"] - cost(bars[last]["c"], slip), last, "flat"


def play(f, sig, p, fill_slot, fill, inside, slip, cfg):
    """One trade from its fill. -> (exit price, exit slot, outcome, entry paid)."""
    e = fill + cost(fill, slip)
    bars = f["bars"]
    if inside:
        if bars[fill_slot]["c"] <= p["stop"]:
            return p["stop"] - cost(p["stop"], slip), fill_slot, "stop", e
        fill_slot += 1
    if fill_slot > cfg["flat_slot"]:
        return None
    xp, xs, oc = walk(bars, fill_slot, p["stop"], p["target"], slip, cfg["flat_slot"])
    return xp, xs, oc, e


# ---------------------------------------------------------------- the portfolio replay

def replay(D1, M5, spy, days, cfg, v, mode="routine", slip=COSTS["medio"], delay=10, cache=None):
    trades = []
    for day in days:
        dd = (cache or {}).get(day) or Day(D1, M5, spy, day, cfg)
        if cache is not None:
            cache[day] = dd
        sigs = []
        for f in dd.universe(v["univ"], v["n"]):
            sg = signal(f, v, cfg)
            if not sg or (v["mkt"] and not dd.spy_up(sg["fill_slot"] - (0 if sg["inside"] else 1))):
                continue
            p = plan(f, sg, v)
            if p:
                sigs.append((f, sg, p))
        state = {"open": [], "taken": set(), "loss": 0.0}

        def take(f, sg, p, fill_slot, fill, inside):
            if len(state["taken"]) >= cfg["trades"] or f["s"] in state["taken"]:
                return
            E = max(p["entry"], fill)
            if fill <= p["stop"] or (p["target"] is not None and fill >= p["target"]):
                return
            if p["target"] is not None and (p["target"] - E) / (E - p["stop"]) < cfg["min_rr"]:
                return
            free = cfg["cash"] - sum(c for c, xs in state["open"] if xs >= fill_slot)
            r = E - p["stop"]
            n = max(0, min(int(cfg["max_loss"] / r + 1e-9), int(cfg["cap"] / E), int(cfg["max_order"] / E),
                           int(free / E)))
            if n < 1 or n * fill < cfg["min_order"]:
                return
            ml = n * r
            if state["loss"] + ml > cfg["day_loss"] + 1e-9:
                return
            out = play(f, sg, p, fill_slot, fill, inside, slip, cfg)
            if not out:
                return
            xp, xs, oc, e = out
            fees = math.ceil(0.0000206 * n * xp * 100) / 100 + math.ceil(0.000003 * 2 * n * 100) / 100
            trades.append({"date": day.isoformat(), "symbol": f["s"], "trigger": "%02d:%02d" % divmod(sg["close_min"], 60),
                           "entry": round(e, 4), "stop": p["stop"], "target": p["target"], "shares": n,
                           "exit": round(xp, 4), "outcome": oc, "R": (xp - e) / r,
                           "pnl": n * (xp - e) - fees, "fees": fees, "cost": n * e})
            state["open"].append((n * e, xs))
            state["taken"].add(f["s"])
            state["loss"] += ml

        if mode == "ideal":
            for f, sg, p in sorted(sigs, key=lambda x: (x[1]["close_min"], -x[0]["dv"])):
                if sg["fill_slot"] in f["bars"]:
                    take(f, sg, p, sg["fill_slot"], sg["fill"], sg["inside"])
        else:
            for rt in [10 * 60 + 15 + 60 * h for h in range(6)]:
                slot = (rt + delay - OPEN_MIN + 4) // 5
                if OPEN_MIN + 5 * slot > cfg["last"] or len(state["taken"]) >= cfg["trades"]:
                    break
                fresh = [x for x in sigs if x[1]["close_min"] <= rt and x[0]["s"] not in state["taken"]]
                fresh.sort(key=lambda x: (-x[1]["close_min"], -x[0]["dv"]))
                for f, sg, p in fresh[:3]:
                    if slot in f["bars"]:
                        take(f, sg, p, slot, round(f["bars"][slot]["o"], 2), False)
    return trades


def summary(trades, budget):
    if not trades:
        return None
    n = len(trades)
    pn = [t["pnl"] for t in trades]
    eq = peak = budget
    mdd = 0.0
    byday = {}
    for t in trades:
        byday[t["date"]] = byday.get(t["date"], 0) + t["pnl"]
    for d in sorted(byday):
        eq += byday[d]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    rng = random.Random(7)
    days = list(byday)
    per = {d: [t["pnl"] for t in trades if t["date"] == d] for d in days}
    boots = sorted(statistics.mean([x for _ in days for x in per[rng.choice(days)]]) for _ in range(2000))
    wins = [x for x in pn if x > 0]
    losses = [x for x in pn if x <= 0]
    return {"trades": n, "days": len(days), "win": len(wins) / n, "loss": len(losses) / n,
            "target": sum(t["outcome"] == "target" for t in trades) / n,
            "stop": sum(t["outcome"] == "stop" for t in trades) / n,
            "flat": sum(t["outcome"] == "flat" for t in trades) / n,
            "avg_R": statistics.mean(t["R"] for t in trades), "exp": statistics.mean(pn),
            "exp_ci": (boots[50], boots[-51]), "avg_win": statistics.mean(wins) if wins else 0.0,
            "avg_loss": statistics.mean(losses) if losses else 0.0, "net": sum(pn),
            "fees": sum(t["fees"] for t in trades), "max_dd": mdd, "end": eq}


def money(x):
    return ("-$%.2f" % -x) if x < 0 else "$%.2f" % x


def pick_variant(a):
    if a.variant not in VARIANTS:
        raise BTError("unknown --variant %s (known: %s)" % (a.variant, ", ".join(VARIANTS)))
    return VARIANTS[a.variant]


def cmd_run(a):
    cfg = read_config()
    v = pick_variant(a)
    D1, M5, spy, days = load_store()
    days = days[a.skip:]
    tr = replay(D1, M5, spy, days, cfg, v, a.mode, COSTS[a.cost], a.delay)
    s = summary(tr, cfg["budget"])
    if a.json:
        print(json.dumps({"variant": a.variant, "rule": v, "mode": a.mode, "cost": a.cost, "first": days[0],
                          "last": days[-1], "summary": s, "trades": tr}, default=str, indent=1))
        return 0
    print("Backtest %s, %s entries, cost %s: %d sessions, %s to %s, $%.0f account (trader/config.json)."
          % (a.variant, a.mode, a.cost, len(days), days[0], days[-1], cfg["budget"]))
    if not s:
        print("No trades.")
        return 1
    print("  trades %d on %d days | won %.1f %% | lost %.1f %% | target %.1f %% stop %.1f %% out at flat-by %.1f %%"
          % (s["trades"], s["days"], 100 * s["win"], 100 * s["loss"], 100 * s["target"], 100 * s["stop"],
             100 * s["flat"]))
    print("  average R %+.3f | per trade %s (95 %% by days: %s to %s) | average win %s, average loss %s"
          % (s["avg_R"], money(s["exp"]), money(s["exp_ci"][0]), money(s["exp_ci"][1]), money(s["avg_win"]),
             money(s["avg_loss"])))
    print("  net %s after %s of Alpaca fees | max drawdown %s | account %s"
          % (money(s["net"]), money(s["fees"]), money(s["max_dd"]), money(s["end"])))
    print("Before exchange, IOF, wire fees and income tax. A replay of past bars, not a prediction.")
    return 0


def signal_R(D1, M5, spy, days, cfg, v, slip, cache):
    """{date: [R of every signal]} with the trigger as entry, no budget limits."""
    out = {}
    for day in days:
        dd = cache.get(day) or Day(D1, M5, spy, day, cfg)
        cache[day] = dd
        rs = []
        for f in dd.universe(v["univ"], v["n"]):
            sg = signal(f, v, cfg)
            if not sg or (v["mkt"] and not dd.spy_up(sg["fill_slot"] - (0 if sg["inside"] else 1))):
                continue
            p = plan(f, sg, v)
            if not p or sg["fill_slot"] not in f["bars"]:
                continue
            o = play(f, sg, p, sg["fill_slot"], sg["fill"], sg["inside"], slip, cfg)
            if o:
                rs.append((o[0] - o[3]) / p["risk"])
        out[day] = rs
    return out


def block_ci(per_day, rng, n=1000):
    ds = [v for v in per_day if v]
    if not ds:
        return None, None
    b = sorted(statistics.mean([x for _ in ds for x in rng.choice(ds)]) for _ in range(n))
    return b[int(0.025 * n)], b[int(0.975 * n) - 1]


def cmd_signals(a):
    cfg = read_config()
    v = pick_variant(a)
    D1, M5, spy, days = load_store()
    days = days[a.skip:]
    R = signal_R(D1, M5, spy, days, cfg, v, COSTS[a.cost], {})
    cut = int(len(days) * a.split)
    rng = random.Random(3)
    print("Every signal of %s, cost %s, trigger as entry, no budget limits:" % (a.variant, a.cost))
    for name, ds in (("first %d sessions" % cut, days[:cut]), ("last %d sessions" % (len(days) - cut), days[cut:]),
                     ("all %d sessions" % len(days), days)):
        rs = [r for d in ds for r in R[d]]
        if not rs:
            print("  %s: no signals" % name)
            continue
        lo, hi = block_ci([R[d] for d in ds], rng)
        print("  %-20s %5d signals  average R %+.3f (95 %% by days %+.3f to %+.3f)  won %.1f %%"
              % (name, len(rs), statistics.mean(rs), lo, hi, 100 * sum(r > 0 for r in rs) / len(rs)))
    return 0


def cmd_grid(a):
    cfg = read_config()
    D1, M5, spy, days = load_store()
    days = days[a.skip:]
    cut = int(len(days) * a.split)
    slip = COSTS[a.cost]
    cache, rows = {}, []
    combos = list(itertools.product(*GRID.values()))
    for i, c in enumerate(combos):
        v = dict(zip(GRID, c))
        R = signal_R(D1, M5, spy, days, cfg, v, slip, cache)
        ins = [r for d in days[:cut] for r in R[d]]
        oos = [r for d in days[cut:] for r in R[d]]
        if len(ins) >= 100 and len(oos) >= 30:
            rows.append(dict(v, n_in=len(ins), r_in=statistics.mean(ins), n_out=len(oos), r_out=statistics.mean(oos)))
        if (i + 1) % 100 == 0:
            print("  %d of %d variants" % (i + 1, len(combos)), file=sys.stderr, flush=True)
    rows.sort(key=lambda r: -r["r_in"])
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    print("%d variants, chosen on sessions %s to %s, checked on %s to %s, cost %s. Best 15 by the first part:"
          % (len(rows), days[0], days[cut - 1], days[cut], days[-1], a.cost))
    for r in rows[:15]:
        print("  %-8s or %2d min  %-5s bull %-5s stop %-6s tgt %-4s spy %-5s | first %4d %+.3fR | last %4d %+.3fR"
              % (r["univ"], 5 * r["n"], r["entry"], r["bull"], r["stop"], ("%dR" % r["tgt"]) if r["tgt"] else "none",
                 r["mkt"], r["n_in"], r["r_in"], r["n_out"], r["r_out"]))
    pos = sum(r["r_out"] > 0 for r in rows)
    print("%d of %d variants were positive on the last part (median %+.3fR). With this many tries some always "
          "look good by luck: trust only a result that holds on new sessions." %
          (pos, len(rows), statistics.median(r["r_out"] for r in rows)))
    return 0


def cmd_today(a):
    """The variant's signals for today's session so far, for paper trading. Never places an order."""
    cfg = read_config()
    v = pick_variant(a)
    if v["univ"] == "rv20":
        raise BTError("today works on the daily list only (variants on 'hits' or 'rv20hits')")
    now = scan.parse_now(a.now) if a.now else scan.parse_now(None)
    et = scan.to_eastern(now)
    day = et.date()
    ok, why = scan.trading_day(day)
    if not ok:
        raise BTError("no session on %s (%s)" % (day, why))
    syms = scan.symbols_from_file(a.watchlist)
    if not syms:
        raise BTError("%s has no symbols: run scan.py daily first" % a.watchlist)
    D1, M5 = {}, {}

    def job(sym):
        doc = {"d1": {}, "m5": {}}
        try:
            merge_chart(doc, scan.yahoo_get(scan.chart_path(sym, "3mo", "1d")), "d1")
            res = json.loads(scan.yahoo_get(scan.chart_path(sym, "60d", "5m")))["chart"]["result"][0]
        except (scan.ScanError, ValueError, KeyError, TypeError, IndexError):
            return sym, None
        _, bars, _ = tm.parse_bars(res, now)   # only bars complete at `now`
        m5 = {}
        for b in bars:
            m5.setdefault(datetime.date.fromisoformat(b["date"]), {})[b["slot"]] = b
        d1 = {datetime.date.fromisoformat(d): tuple(x) for d, x in doc["d1"].items()}
        return sym, (d1, m5)

    with concurrent.futures.ThreadPoolExecutor(a.threads) as ex:
        for sym, got in ex.map(job, sorted(set(syms) | {"SPY"})):
            if got:
                D1[sym], M5[sym] = got
    spy = M5.pop("SPY", {})
    D1.pop("SPY", None)
    dd = Day(D1, M5, spy, day, cfg)
    univ = dd.universe(v["univ"], v["n"])
    n = v["n"]
    range_end = OPEN_MIN + 5 * n
    print("%s on %s, %s ET: %d of %d names fetched; the %d with the most opening volume against their last 14 "
          "sessions:" % (a.variant, day, et.strftime("%H:%M"), len(D1), len(set(syms)), len(univ)))
    if et.hour * 60 + et.minute < range_end:
        print("  The %d-minute opening range is still forming (ends %02d:%02d ET)." % (5 * n, *divmod(range_end, 60)))
    spy_now = max(spy.get(day, {}) or {-1: None})
    print("  SPY %s on the day so far." % ("UP" if spy_now >= 0 and dd.spy_up(spy_now) else "not up"))
    for f in univ:
        bars = f["bars"]
        if not all(i in bars for i in range(n)):
            print("  %-6s rvol %4.1fx  range not complete yet" % (f["s"], f["rv"].get(n, 0)))
            continue
        oh = max(bars[i]["h"] for i in range(n))
        sg = signal(f, v, cfg)
        if sg:
            p = plan(f, sg, v)
            spy_ok = (not v["mkt"]) or dd.spy_up(sg["fill_slot"] - (0 if sg["inside"] else 1))
            at = sg["close_min"] - (5 if sg["inside"] else 0)
            size = int(min(cfg["max_loss"] / p["risk"], cfg["cap"] / p["entry"], cfg["max_order"] / p["entry"],
                           cfg["cash"] / p["entry"])) if p else 0
            print("  %-6s rvol %4.1fx  TRIGGERED %02d:%02d at %.2f  stop %.2f (%.0f %% ATR)  %d shares%s"
                  % (f["s"], f["rv"].get(n, 0), *divmod(at, 60), sg["fill"],
                     p["stop"] if p else 0, float(v["stop"][3:]) if v["stop"].startswith("atr") else 0, size,
                     "" if spy_ok else "  (SPY was not up: the rule skips it)"))
        else:
            stop = tm.cents_away(oh + 0.01 - float(v["stop"][3:]) / 100 * f["atr"], True) \
                if v["stop"].startswith("atr") else None
            print("  %-6s rvol %4.1fx  waiting: buy-stop %.2f%s" % (f["s"], f["rv"].get(n, 0), oh + 0.01,
                                                                    ("  stop %.2f" % stop) if stop else ""))
    print("Paper only: %s is an experiment near break-even after costs in the 2026-10-22 backtest, not a proven "
          "edge. No target; out by %02d:%02d ET. Nothing was placed."
          % (a.variant, *divmod(OPEN_MIN + 5 * (cfg["flat_slot"] + 1), 60)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backtest.py", description="Replay the trader routine on past bars.")
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch")
    f.add_argument("--symbols")
    f.add_argument("--threads", type=int, default=6)
    f.add_argument("--max-price", type=float, default=160)
    for name in ("run", "signals", "grid"):
        p = sub.add_parser(name)
        p.add_argument("--variant", default="current")
        p.add_argument("--cost", default="medio", choices=list(COSTS))
        p.add_argument("--skip", type=int, default=6, help="first sessions left out (relative volume needs history)")
        p.add_argument("--json", action="store_true")
        if name == "run":
            p.add_argument("--mode", default="routine", choices=["routine", "ideal"])
            p.add_argument("--delay", type=int, default=10)
        else:
            p.add_argument("--split", type=float, default=0.6)
    t = sub.add_parser("today")
    t.add_argument("--variant", default="sip30")
    t.add_argument("--watchlist", default="trader/watchlist.json")
    t.add_argument("--now", help='New York time, e.g. "2026-10-21 11:15"')
    t.add_argument("--threads", type=int, default=6)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"fetch": cmd_fetch, "run": cmd_run, "signals": cmd_signals, "grid": cmd_grid,
                "today": cmd_today}[a.cmd](a)
    except BTError as e:
        print("backtest.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

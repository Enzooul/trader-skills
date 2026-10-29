#!/usr/bin/env python3
"""ibkr_chart.py: hand Interactive Brokers data to the trader-* scripts.

The scripts cannot call the IBKR connector (it is a Claude tool, not a URL),
so Claude reads IBKR with the connector and passes the numbers here:

  bars   IBKR price bars -> a Yahoo v8 chart JSON, the format every script
         already reads with --offline. Writes DIR/<SYM>.json (trader-entry,
         trader-exit, trader-scanner orb) and DIR/<SYM>-60d5m.json
         (trader-timemachine).
  price  one IBKR quote -> trader/prices.json (trader-budget, trader-risk),
         merged with what is already there.
  account  the IBKR balance (get_account_summary) and open positions
         (get_account_positions) -> trader/account.json. With
         "budget_source": "ibkr" in trader/config.json, trader-budget and
         trader-risk size every trade from this balance, never from "budget".

Usage:
    python3 ibkr_chart.py bars --in aapl_bars.json --out trader/ibkr
    python3 ibkr_chart.py price --symbol AAPL --price 349.26 --time 1793281748 --status REALTIME
    python3 ibkr_chart.py account --net-liq 357.48 --cash 357.48 [--position AAPL=2@349.10]

The bars input is a small JSON that Claude writes from get_price_history,
one object per bar, oldest first, each bar stamped at its START:

    {"symbol": "AAPL", "interval": "5m", "prev_close": 351.10,
     "bars": [{"t": 1793280600, "o": 349.0, "h": 349.6, "l": 348.8, "c": 349.3, "v": 120400}, ...]}

"t" is Unix time in seconds (milliseconds are accepted and divided by 1000).
"prev_close" is optional. Nothing here talks to the network or the broker.
"""

import argparse
import datetime as dt
import json
import os
import sys

INTERVALS = {"1m": 60, "5m": 300}


class IBKRError(Exception):
    pass


def _secs(t):
    try:
        t = float(t)
    except (TypeError, ValueError):
        raise IBKRError("bar time %r is not a Unix timestamp" % (t,))
    if t > 1e12:
        t /= 1000.0
    return int(t)


def to_chart(doc):
    """{"symbol","interval","bars":[{t,o,h,l,c,v}],"prev_close"?} -> Yahoo v8 chart dict."""
    sym = str(doc.get("symbol") or "").upper().strip()
    if not sym:
        raise IBKRError("the input has no symbol")
    interval = str(doc.get("interval") or "5m")
    if interval not in INTERVALS:
        raise IBKRError("interval %s: only %s" % (interval, ", ".join(sorted(INTERVALS))))
    rows = []
    for b in doc.get("bars") or []:
        try:
            o, h, l, c = (float(b[k]) for k in ("o", "h", "l", "c"))
        except (KeyError, TypeError, ValueError):
            raise IBKRError("bar %r needs numbers o, h, l and c" % (b,))
        rows.append((_secs(b.get("t")), o, h, l, c, int(float(b.get("v") or 0))))
    if not rows:
        raise IBKRError("no bars for %s" % sym)
    rows.sort()
    dedup = {}
    for r in rows:
        dedup[r[0]] = r
    rows = [dedup[t] for t in sorted(dedup)]
    step = INTERVALS[interval]
    last = rows[-1]
    meta = {"symbol": sym.replace(".", "-"), "currency": "USD", "exchangeTimezoneName": "America/New_York",
            "dataGranularity": interval, "regularMarketPrice": last[4],
            "regularMarketTime": last[0] + step, "regularMarketVolume": sum(r[5] for r in rows),
            "source": "ibkr"}
    if doc.get("prev_close") is not None:
        meta["previousClose"] = meta["chartPreviousClose"] = float(doc["prev_close"])
    quote = {"open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
             "close": [r[4] for r in rows], "volume": [r[5] for r in rows]}
    return {"chart": {"result": [{"meta": meta, "timestamp": [r[0] for r in rows],
                                  "indicators": {"quote": [quote]}}], "error": None}}


def _write(path, doc):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def cmd_bars(a):
    try:
        with open(a.inp, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise IBKRError("could not read %s: %s" % (a.inp, e))
    chart = to_chart(doc)
    meta = chart["chart"]["result"][0]["meta"]
    sym = meta["symbol"]
    paths = [os.path.join(a.out, "%s.json" % sym)]
    if meta["dataGranularity"] == "5m":
        paths.append(os.path.join(a.out, "%s-60d5m.json" % sym))
    for p in paths:
        _write(p, chart)
    n = len(chart["chart"]["result"][0]["timestamp"])
    print("%s: %d %s bars from IBKR, last close %.2f -> %s" % (sym, n, meta["dataGranularity"],
                                                            meta["regularMarketPrice"], ", ".join(paths)))


def cmd_price(a):
    old = {}
    if os.path.exists(a.write):
        try:
            with open(a.write, encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = {}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    when = dt.datetime.fromtimestamp(_secs(a.time), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if a.time else now
    sym = a.symbol.upper()
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    prices[sym] = float(a.price)
    detail[sym] = {"prev_close": a.prev_close, "time": when, "source": "ibkr", "status": a.status,
                   "fetched": now}
    doc = {"as_of": now, "source": "ibkr",
           "note": "Interactive Brokers quotes (connector get_price_snapshot). detail.status says REALTIME "
                   "or DELAYED; detail.time is the quote's own time.",
           "prices": prices, "detail": detail}
    _write(a.write, doc)
    print("%s $%.2f (IBKR, %s, %s) -> %s" % (sym, prices[sym], a.status or "status unknown", when, a.write))


def _position(text):
    """SYM=QTY or SYM=QTY@AVG_COST -> {"ticker", "shares", "lots"}."""
    m = text.strip().upper().split("=")
    if len(m) != 2 or not m[0]:
        raise IBKRError("--position wants SYM=QTY or SYM=QTY@AVG_COST, not %r" % text)
    qty, _, avg = m[1].partition("@")
    try:
        qty = float(qty)
        avg = float(avg) if avg else None
    except ValueError:
        raise IBKRError("--position wants SYM=QTY or SYM=QTY@AVG_COST, not %r" % text)
    pos = {"ticker": m[0], "shares": qty}
    if avg is not None:
        pos["lots"] = [{"shares": qty, "cost": round(qty * avg, 2)}]
    return pos


def cmd_account(a):
    if a.net_liq <= 0:
        raise IBKRError("--net-liq must be above zero (net_liquidation from get_account_summary)")
    cash = a.net_liq if a.cash is None else a.cash
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"as_of": now, "source": "ibkr", "currency": "USD",
           "note": "IBKR get_account_summary (net_liquidation, total_cash_value) and get_account_positions. "
                   "trader-budget and trader-risk only trust it on the New York day of as_of.",
           "net_liquidation": round(float(a.net_liq), 2), "cash": round(float(cash), 2),
           "positions": [_position(x) for x in a.position or []]}
    _write(a.write, doc)
    print("IBKR balance %s, cash %s, %d open position(s) -> %s" % (
        "$%.2f" % doc["net_liquidation"], "$%.2f" % doc["cash"], len(doc["positions"]), a.write))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hand IBKR connector data to the trader-* scripts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bars", help="IBKR bars -> Yahoo v8 chart files for --offline")
    b.add_argument("--in", dest="inp", required=True, help="bars JSON written from get_price_history")
    b.add_argument("--out", default="trader/ibkr", help="folder for <SYM>.json and <SYM>-60d5m.json")
    p = sub.add_parser("price", help="one IBKR quote -> trader/prices.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--price", required=True, type=float, help="last (or mid of bid/ask if last is empty)")
    p.add_argument("--time", help="the quote's Unix time (last.ts)")
    p.add_argument("--status", help="top-status: REALTIME, DELAYED, FROZEN...")
    p.add_argument("--prev-close", type=float)
    p.add_argument("--write", default="trader/prices.json")
    c = sub.add_parser("account", help="IBKR balance and positions -> trader/account.json")
    c.add_argument("--net-liq", required=True, type=float, help="net_liquidation from get_account_summary")
    c.add_argument("--cash", type=float, help="total_cash_value (default: the same as --net-liq)")
    c.add_argument("--position", action="append", help="SYM=QTY or SYM=QTY@AVG_COST, once per open position")
    c.add_argument("--write", default="trader/account.json")
    a = ap.parse_args(argv)
    try:
        {"bars": cmd_bars, "price": cmd_price, "account": cmd_account}[a.cmd](a)
    except IBKRError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

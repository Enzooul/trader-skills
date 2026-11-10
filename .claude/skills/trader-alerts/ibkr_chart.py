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
  screener an IBKR preview_screener answer -> trader/ibkr-screener.txt (a ticker
         list for scan.py orb --watchlist) and .json (with contract ids).
  account  the IBKR balance (get_account_summary) and open positions
         (get_account_positions) -> trader/account.json. With
         "budget_source": "ibkr" in trader/config.json, trader-budget and
         trader-risk size every trade from this balance, never from "budget".

Usage:
    python3 ibkr_chart.py bars --in aapl_bars.json --out trader/ibkr
    python3 ibkr_chart.py bars --in ibkr_answer.json --symbol AAPL --prev-close 351.10
    python3 ibkr_chart.py price --symbol AAPL --price 349.26 --time 1793281748 --status REALTIME
    python3 ibkr_chart.py account --net-liq 357.48 --cash 357.48 [--position AAPL=2@349.10]
    python3 ibkr_chart.py account --net-liq 528.73 --cash 528.73 --positions pos.json \\
        --orders orders.json --trades trades.json

account: --positions, --orders and --trades take the raw get_account_positions,
get_account_orders and get_account_trades answers saved as-is. --orders runs
the stop guard (every open position needs a working sell stop at the broker);
--trades records today's entries and realized P&L, which trader-risk counts
in the daily limits (it refuses a balance without them).

The bars input is either the raw get_price_history answer saved as-is
(columns "time", "open", "high", "low", "close", "volume", ISO times at each
bar's START, plus "chart_step" and "chart_end"; pass --symbol, since the
answer does not name it), or a small JSON one object per bar, oldest first:

    {"symbol": "AAPL", "interval": "5m", "prev_close": 351.10,
     "bars": [{"t": 1793280600, "o": 349.0, "h": 349.6, "l": 348.8, "c": 349.3, "v": 120400}, ...]}

"t" is Unix time in seconds (milliseconds are accepted and divided by 1000).
"prev_close" is optional. Nothing here talks to the network or the broker.
"""

import argparse
import datetime as dt
import json
import os
import re
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


def _iso_secs(text):
    try:
        return int(dt.datetime.strptime(str(text).replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        raise IBKRError("time %r is not like 2026-10-29T13:55:00Z" % (text,))


def from_ibkr(doc, symbol, prev_close=None):
    """Raw get_price_history answer (columns) -> the bars input to_chart takes.
    Seen 2026-10-29: {"chart_step":300,"chart_end":"...Z","time":[...],"open":[...],
    "high","low","close","volume"}; times are each bar's start."""
    times = doc.get("time") or []
    cols = {k: doc.get(k) or [] for k in ("open", "high", "low", "close", "volume")}
    if any(len(v) != len(times) for v in cols.values()):
        raise IBKRError("the IBKR answer has columns of different lengths")
    interval = {60: "1m", 300: "5m"}.get(int(doc.get("chart_step") or 0))
    if not interval:
        raise IBKRError("chart_step %s: only ONE_MIN (60) or FIVE_MINS (300) bars" % doc.get("chart_step"))
    bars = [{"t": _iso_secs(t), "o": cols["open"][i], "h": cols["high"][i], "l": cols["low"][i],
             "c": cols["close"][i], "v": cols["volume"][i]} for i, t in enumerate(times)]
    out = {"symbol": symbol, "interval": interval, "bars": bars, "prev_close": prev_close}
    if doc.get("chart_end"):
        out["as_of"] = _iso_secs(doc["chart_end"])
    return out


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
            "regularMarketTime": min(last[0] + step, int(doc.get("as_of") or last[0] + step)), "regularMarketVolume": sum(r[5] for r in rows),
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
    if isinstance(doc, dict) and "time" in doc and "close" in doc:
        if not a.symbol:
            raise IBKRError("a raw IBKR answer does not name the stock: pass --symbol")
        doc = from_ibkr(doc, a.symbol.upper(), a.prev_close)
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


# ---------------------------------------------------------------- New York day (no tz files)

def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def ny_date(utc):
    """naive or aware UTC datetime -> the New York calendar date (US DST rule since 2007)."""
    naive = utc.replace(tzinfo=None)
    start = dt.datetime.combine(_nth_sunday(naive.year, 3, 2), dt.time(7, 0))
    end = dt.datetime.combine(_nth_sunday(naive.year, 11, 1), dt.time(6, 0))
    return (naive + dt.timedelta(hours=-4 if start <= naive < end else -5)).date()


def _utc(text):
    try:
        return dt.datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise IBKRError("time %r is not like 2026-11-05T15:41:38Z" % (text,))


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise IBKRError("cannot read the %s answer %s: %s" % (what, path, e))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def positions_from_ibkr(doc):
    """Raw get_account_positions answer -> positions (closed rows with position 0 dropped).
    Seen 2026-11-05: {"positions":[{"contract_description":"ACAD","position":0,
    "average_price":0,"asset_class":"STK",...}]}. average_price includes the commission."""
    out = []
    for p in (doc.get("positions") if isinstance(doc, dict) else doc) or []:
        qty = _num(p.get("position"))
        sym = str(p.get("contract_description") or p.get("symbol") or "").upper().strip()
        if not sym or not qty:
            continue
        pos = {"ticker": sym, "shares": qty}
        avg = _num(p.get("average_price"))
        if avg:
            pos["lots"] = [{"shares": qty, "cost": round(qty * avg, 2)}]
        out.append(pos)
    return out


DONE_ORDER = {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "INACTIVE", "API_CANCELLED"}
STOP_TYPES = {"STOP", "STP", "STOP_LIMIT", "STP_LMT", "TRAIL", "TRAILING_STOP"}


def stops_from_orders(doc):
    """Raw get_account_orders answer -> {SYM: [(stop_price, shares)]} for WORKING sell stops.
    Seen 2026-11-05: orders carry no symbol field; it is the last word of
    primary_description ("Sold 19 ACAD"), and the stop price is in
    secondary_description ("STP 21.72, Day")."""
    out = {}
    for o in (doc.get("orders") if isinstance(doc, dict) else doc) or []:
        if str(o.get("side", "")).upper() != "SELL":
            continue
        if str(o.get("order_type", "")).upper() not in STOP_TYPES:
            continue
        if str(o.get("order_status", "")).upper() in DONE_ORDER:
            continue
        sym = str(o.get("symbol") or "").upper().strip()
        if not sym:
            words = str(o.get("primary_description") or "").split()
            sym = words[-1].upper() if words else ""
        price = _num(o.get("stop_price") or o.get("aux_price"))
        if not price:
            m = re.search(r"(?:STP|STOP)\s+([0-9]+(?:\.[0-9]+)?)", str(o.get("secondary_description") or ""), re.I)
            price = float(m.group(1)) if m else None
        qty = _num(o.get("remaining_shares_qty")) or _num(o.get("total_shares_qty")) or 0
        if sym:
            out.setdefault(sym, []).append((price, qty))
    return out


def day_from_trades(doc, day, held):
    """Raw get_account_trades answer -> what happened on the New York day `day`:
    entries (distinct buy orders), realized P&L (IBKR's realized_pnl of the sells,
    commissions of both legs included) and the commission already paid on buys
    that are still open (not in realized_pnl yet)."""
    fills, buys, realized, open_comm, missing = [], set(), 0.0, 0.0, 0
    for t in (doc.get("trades") if isinstance(doc, dict) else doc) or []:
        if str(t.get("sec_type") or "STK").upper() != "STK":
            continue
        when = _utc(t.get("trade_time"))
        if ny_date(when) != day:
            continue
        side = str(t.get("side", "")).upper()
        sym = str(t.get("symbol", "")).upper()
        comm = _num(t.get("commission")) or 0.0
        pnl = _num(t.get("realized_pnl"))
        fills.append({"symbol": sym, "side": side, "order_id": str(t.get("order_id") or ""),
                      "size": _num(t.get("size")), "price": _num(t.get("price")),
                      "time": str(t.get("trade_time")), "commission": round(comm, 4),
                      "realized_pnl": None if pnl is None else round(pnl, 4)})
        if side == "BUY":
            buys.add(str(t.get("order_id") or t.get("trade_id")))
            if sym in held:
                open_comm += comm
        elif side == "SELL":
            if pnl is None:
                missing += 1
                pnl = -comm
            realized += pnl
    return {"date": day.isoformat(), "source": "ibkr get_account_trades", "entries": len(buys),
            "realized": round(realized, 2), "open_commission": round(open_comm, 2),
            "sells_without_realized_pnl": missing, "fills": fills}


def cmd_account(a):
    if a.net_liq <= 0:
        raise IBKRError("--net-liq must be above zero (net_liquidation from get_account_summary)")
    cash = a.net_liq if a.cash is None else a.cash
    now_dt = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    positions = positions_from_ibkr(_load(a.positions, "get_account_positions")) if a.positions else []
    have = {p["ticker"] for p in positions}
    positions += [p for p in (_position(x) for x in a.position or []) if p["ticker"] not in have]
    doc = {"as_of": now, "source": "ibkr", "currency": "USD",
           "note": "IBKR get_account_summary (net_liquidation, total_cash_value), get_account_positions, "
                   "get_account_orders and get_account_trades. trader-budget and trader-risk only trust it "
                   "on the New York day of as_of.",
           "net_liquidation": round(float(a.net_liq), 2), "cash": round(float(cash), 2),
           "positions": positions}
    guard = []
    if a.orders:
        stops = stops_from_orders(_load(a.orders, "get_account_orders"))
        doc["stops_checked"] = True
        for p in positions:
            legs = stops.get(p["ticker"], [])
            covered = sum(q for _, q in legs)
            prices = [x for x, _ in legs if x]
            if prices:
                p["stop"] = max(prices)
            p["stop_shares"] = covered
            if covered >= p["shares"] - 1e-9 and prices:
                guard.append("OK       %s: %s shares, stop %.2f at the broker" % (p["ticker"], _fmt(p["shares"]), p["stop"]))
            elif covered > 0:
                guard.append("PARTIAL  %s: %s shares but the stop at the broker covers only %s" % (
                    p["ticker"], _fmt(p["shares"]), _fmt(covered)))
            else:
                guard.append("NO STOP  %s: %s shares and no working sell stop at the broker" % (
                    p["ticker"], _fmt(p["shares"])))
    if a.trades:
        # IBKR's average_price already includes the buy commission; a --position cost may not
        doc["today"] = day_from_trades(_load(a.trades, "get_account_trades"), ny_date(now_dt),
                                       {p["ticker"] for p in positions if p["ticker"] not in have})
    _write(a.write, doc)
    print("IBKR balance %s, cash %s, %d open position(s) -> %s" % (
        "$%.2f" % doc["net_liquidation"], "$%.2f" % doc["cash"], len(doc["positions"]), a.write))
    if "today" in doc:
        t = doc["today"]
        print("Today (%s, New York): %d entr%s, realized %s, open-buy commission $%.2f" % (
            t["date"], t["entries"], "y" if t["entries"] == 1 else "ies",
            ("-$%.2f" % -t["realized"]) if t["realized"] < 0 else "$%.2f" % t["realized"], t["open_commission"]))
    else:
        print("WARNING: no --trades: trader-risk refuses without today's IBKR trades (get_account_trades)")
    if a.orders:
        print("STOP GUARD:" if positions else "STOP GUARD: no open positions")
        for g in guard:
            print("  " + g)
        if any(not g.startswith("OK") for g in guard):
            print("ALERT: a position is not fully protected. Say it FIRST in the message to the user.")
    elif positions:
        print("WARNING: no --orders: the stops of the open positions were not checked")


def _fmt(x):
    return str(int(x)) if float(x) == int(x) else "%.4f" % x


def cmd_screener(a):
    """Raw preview_screener / run_saved_screener answer -> a ticker list for scan.py
    (--watchlist FILE.txt) and a JSON with the IBKR contract ids (no search_contracts
    needed later). Seen 2026-11-06: {"results":[{"rank":1,"contract_id":445419999,
    "company_name":"JFROG LTD","contract_description":"FROG"}],"total_matched":293};
    the answer carries no prices or percentages, only the ranked names."""
    doc = _load(a.inp, "preview_screener")
    rows = (doc.get("results") if isinstance(doc, dict) else doc) or []
    hits = []
    for r in rows:
        sym = str(r.get("contract_description") or r.get("symbol") or "").upper().strip()
        if not sym or " " in sym:
            continue
        hits.append({"symbol": sym, "contract_id": r.get("contract_id"), "rank": r.get("rank"),
                     "name": r.get("company_name")})
    if not hits:
        raise IBKRError("the screener answer has no results")
    hits.sort(key=lambda h: (h["rank"] is None, h["rank"]))
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"as_of": now, "source": "ibkr preview_screener", "filters": a.label or "",
           "total_matched": doc.get("total_matched") if isinstance(doc, dict) else None, "hits": hits}
    _write(a.out, out)
    txt = os.path.splitext(a.out)[0] + ".txt"
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h["symbol"] for h in hits) + "\n")
    print("IBKR screener: %d of %s matches -> %s and %s (scan.py orb --watchlist %s)" % (
        len(hits), out["total_matched"] if out["total_matched"] is not None else "?", a.out, txt, txt))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hand IBKR connector data to the trader-* scripts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bars", help="IBKR bars -> Yahoo v8 chart files for --offline")
    b.add_argument("--in", dest="inp", required=True, help="bars JSON written from get_price_history")
    b.add_argument("--out", default="trader/ibkr", help="folder for <SYM>.json and <SYM>-60d5m.json")
    b.add_argument("--symbol", help="the ticker, needed with a raw IBKR answer")
    b.add_argument("--prev-close", type=float, help="yesterday's close, if known")
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
    c.add_argument("--positions", help="raw get_account_positions answer saved as-is (instead of --position)")
    c.add_argument("--orders", help="raw get_account_orders answer: checks every open position has a stop")
    c.add_argument("--trades", help="raw get_account_trades answer (period DAYS_7): today's entries and realized P&L")
    c.add_argument("--write", default="trader/account.json")
    r = sub.add_parser("screener", help="IBKR preview_screener answer -> ticker list for scan.py orb")
    r.add_argument("--in", dest="inp", required=True, help="the preview_screener answer saved as-is")
    r.add_argument("--out", default="trader/ibkr-screener.json")
    r.add_argument("--label", help="a short note of the filters used")
    a = ap.parse_args(argv)
    try:
        {"bars": cmd_bars, "price": cmd_price, "account": cmd_account, "screener": cmd_screener}[a.cmd](a)
    except IBKRError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

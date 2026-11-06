#!/usr/bin/env python3
"""preflight.py: is the operation safe to run today? Green / yellow / red.

Run it at the start of the Pre-market and of every Radar, after writing
trader/account.json (ibkr_chart.py account ... --orders ... --trades ...).
Claude tests the IBKR connector first (one get_price_snapshot and one
get_price_history on AAPL, contract 265598) and passes the outcome, since
this script cannot call the connector itself:

    python3 preflight.py --quote ok --bars fail
    python3 preflight.py --quote ok --bars ok --stage radar --json

Checks:
  clock     New York date and time, trading day, session open or not
  ibkr      the connector answered a quote and bars (what Claude saw)
  balance   trader/account.json exists and is from today (New York)
  trades    today's IBKR trades are in it (trader-risk refuses without them)
  stops     every open position has a working sell stop covering its shares
  overnight a position open in the Pre-market (it was not flat by the close)
  journal   the trading-journal folder is there and writable
  code      trader-journal exists and this checkout matches origin/main

RED means do not plan trades until it is fixed: say it FIRST to the user.
YELLOW means go on, but say what is degraded (for example, bars from Yahoo).
Exit code: 0 green, 1 yellow, 2 red. Nothing here talks to the network or
the broker; git is only read (never fetched).
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILLS, "trader-risk"))
import check as risk  # noqa: E402  (calendar and New York clock, no network)

GREEN, YELLOW, RED = "VERDE", "AMARELO", "VERMELHO"
RANK = {GREEN: 0, YELLOW: 1, RED: 2}


def _now(text):
    if text:
        return risk.parse_now(text)
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def check_clock(now_utc, stage):
    et = risk.to_eastern(now_utc)
    ok, why = risk.trading_day(et.date())
    stamp = et.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return YELLOW, "mercado fechado hoje (%s, %s em Nova York): sem trades" % (why, stamp)
    if et.year not in risk.CALENDAR_YEARS:
        return YELLOW, "a lista de feriados não cobre %d: confira nyse.com" % et.year
    t = et.time()
    close = risk.EARLY if et.date().isoformat() in risk.NYSE_EARLY_CLOSE else risk.CLOSE
    if stage == "radar" and (t < risk.OPEN or t >= close):
        return YELLOW, ("o Radar rodou fora do pregão (%s em Nova York, pregão %s-%s): o horário da rotina "
                        "está errado" % (stamp, risk.OPEN.strftime("%H:%M"), close.strftime("%H:%M")))
    return GREEN, "%s em Nova York, dia de pregão" % stamp


def check_ibkr(quote, bars):
    if quote == "fail":
        return RED, "o conector da IBKR não respondeu à cotação: sem preço confiável, não planejar trades"
    if quote is None:
        return YELLOW, "o conector da IBKR não foi testado (passe --quote ok|fail)"
    if bars == "fail":
        return YELLOW, "a IBKR deu cotação mas não barras: as barras vêm do Yahoo (reserva), avise o usuário"
    if bars is None:
        return YELLOW, "as barras da IBKR não foram testadas (passe --bars ok|fail)"
    return GREEN, "IBKR respondeu cotação e barras"


def check_account(acct, path, today):
    """-> ([(name, level, text)] for balance, trades and stops, open positions)."""
    out = []
    if acct is None:
        out.append(("balance", RED, "não há %s: leia o saldo (get_account_summary) e rode ibkr_chart.py account"
                    % path))
        return out, []
    try:
        as_of = dt.datetime.strptime(str(acct["as_of"])[:19], "%Y-%m-%dT%H:%M:%S")
        day = risk.to_eastern(as_of).date().isoformat()
        bal = float(acct["net_liquidation"])
    except (KeyError, ValueError, TypeError):
        out.append(("balance", RED, "%s não é um arquivo do ibkr_chart.py account" % path))
        return out, []
    if day != today:
        out.append(("balance", RED, "o saldo em %s é de %s, não de hoje (%s): leia de novo" % (path, day, today)))
    else:
        out.append(("balance", GREEN, "saldo de hoje: US$ %.2f, caixa US$ %.2f"
                    % (bal, float(acct.get("cash", bal)))))
    t = acct.get("today") if isinstance(acct.get("today"), dict) else None
    if not t or t.get("date") != today:
        out.append(("trades", RED, "faltam os trades de hoje da IBKR (get_account_trades DAYS_7 + --trades): "
                    "sem eles o trader-risk recusa e o limite do dia não conta o que foi feito"))
    else:
        r = float(t.get("realized") or 0)
        out.append(("trades", GREEN, "hoje: %d entrada(s), realizado %sUS$ %.2f"
                    % (int(t.get("entries") or 0), "-" if r < 0 else "+", abs(r))))
    pos = [p for p in acct.get("positions") or [] if float(p.get("shares") or 0) > 0]
    if not pos:
        out.append(("stops", GREEN, "nenhuma posição aberta"))
    elif not acct.get("stops_checked"):
        out.append(("stops", RED, "há posição aberta (%s) e os stops não foram conferidos: rode ibkr_chart.py "
                    "account com --orders" % ", ".join(p["ticker"] for p in pos)))
    else:
        bad = []
        for p in pos:
            covered = float(p.get("stop_shares") or 0)
            if not p.get("stop") or covered < float(p["shares"]) - 1e-9:
                bad.append("%s (%s ações, stop cobre %s)" % (p["ticker"], _n(p["shares"]), _n(covered)))
        if bad:
            out.append(("stops", RED, "SEM STOP COMPLETO NA CORRETORA: " + "; ".join(bad)))
        else:
            out.append(("stops", GREEN, "toda posição aberta tem stop: " + ", ".join(
                "%s %s @ %.2f" % (p["ticker"], _n(p["shares"]), float(p["stop"])) for p in pos)))
    return out, pos


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.4f" % x


def check_overnight(pos, stage):
    if stage != "premarket" or not pos:
        return None
    return ("overnight", RED, "posição aberta antes da abertura (%s): ela passou da noite, contra a regra de "
            "zerar até %s. Decida com o usuário o que fazer antes de qualquer plano novo"
            % (", ".join("%s %s" % (p["ticker"], _n(p["shares"])) for p in pos), "15:55"))


def check_journal(cfg):
    d = os.environ.get("TRADER_JOURNAL_DIR") or ((cfg or {}).get("journal") or {}).get("dir")
    if not d:
        return YELLOW, "trader/config.json não diz onde fica o diário (journal.dir)"
    if not os.path.isdir(d):
        return RED, "a pasta do diário %s não existe nesta sessão: o aprendizado não seria salvo" % d
    probe = os.path.join(d, ".preflight")
    try:
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as e:
        return RED, "a pasta do diário %s não aceita escrita: %s" % (d, e)
    return GREEN, "diário acessível em %s" % d


def _git(*args):
    try:
        return subprocess.run(("git",) + args, cwd=SKILLS, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_code():
    if not os.path.isfile(os.path.join(SKILLS, "trader-journal", "journal.py")):
        return YELLOW, "trader-journal não está nesta cópia do repositório (o PR do diário não entrou na main?)"
    head, main = _git("rev-parse", "HEAD"), _git("rev-parse", "origin/main")
    if not head:
        return YELLOW, "não deu para ler a versão do código (git)"
    if main and head != main:
        return YELLOW, "o código em uso (%s) não é o da main (%s)" % (head[:7], main[:7])
    return GREEN, "código = main (%s)" % head[:7]


def run(a):
    now = _now(a.now)
    today = risk.to_eastern(now).date().isoformat()
    cfg = _load(a.config) or {}
    rows = []
    lvl, txt = check_clock(now, a.stage)
    rows.append(("clock", lvl, txt))
    lvl, txt = check_ibkr(a.quote, a.bars)
    rows.append(("ibkr", lvl, txt))
    acct = _load(a.account)
    acc_rows, pos = check_account(acct, a.account, today)
    rows.extend(acc_rows)
    o = check_overnight(pos, a.stage)
    if o:
        rows.append(o)
    lvl, txt = check_journal(cfg)
    rows.append(("journal", lvl, txt))
    lvl, txt = check_code()
    rows.append(("code", lvl, txt))
    overall = max((r[1] for r in rows), key=RANK.get)
    return {"date": today, "stage": a.stage, "overall": overall,
            "checks": [{"check": n, "level": l, "text": t} for n, l, t in rows]}


def report(res):
    lines = ["PRÉ-VOO %s (%s): %s" % (res["date"], res["stage"], res["overall"])]
    for c in sorted(res["checks"], key=lambda c: -RANK[c["level"]]):
        lines.append("  %-8s %-9s %s" % (c["level"], c["check"], c["text"]))
    if res["overall"] == RED:
        lines.append("VERMELHO: diga isso na PRIMEIRA linha ao usuário e não planeje trades até resolver.")
    elif res["overall"] == YELLOW:
        lines.append("AMARELO: siga, mas diga ao usuário o que está degradado.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pre-flight check before planning trades (green/yellow/red).")
    ap.add_argument("--quote", choices=("ok", "fail"), help="did IBKR get_price_snapshot answer?")
    ap.add_argument("--bars", choices=("ok", "fail"), help="did IBKR get_price_history answer?")
    ap.add_argument("--stage", choices=("premarket", "radar", "flat"), default="radar")
    ap.add_argument("--account", default="trader/account.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--now", help="pretend it is this New York time, e.g. \"2026-11-06 09:00\"")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(a)
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return RANK[res["overall"]]


if __name__ == "__main__":
    sys.exit(main())

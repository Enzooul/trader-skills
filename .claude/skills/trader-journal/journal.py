#!/usr/bin/env python3
"""journal.py: the trading journal (diario de operacoes) and the learning loop.

Every plan the routines show (Pre-mercado, Radar) becomes one row. The row
then collects what happened to it: the decision (operated, declined, never
confirmed), the real IBKR fills when Cristian traded it, and a simulated
result when he did not, so a plan that was not taken still teaches something.
The weekly review reads the rows and suggests rule changes only when the
sample is big enough; it never changes a rule by itself.

Where it lives: the project's shared folder (/mnt/project-files/diario by
default), not the git repository. The repository is public and the journal
holds real dollars and the account's trades; the shared folder is private,
outlives every session and every routine thread of the project can read and
write it. Override with --dir, TRADER_JOURNAL_DIR or "journal": {"dir": ...}
in trader/config.json.

Commands:
  plan         record (or update) a plan shown by a routine
  decide       record the decision on a plan (operado, recusado, nao_confirmado, ignorado)
  ibkr-trades  import a raw get_account_trades answer: fills -> real results
  simulate     resolve the plans of one day on 5-minute bars (ibkr_chart.py bars output)
  note         add an observation (or an approved rule) to licoes.md
  lessons      the short briefing a routine reads before it decides
  stats        the numbers behind the lessons, by bucket
  review       the weekly review (markdown in revisoes/), proposals only with enough sample
  show         list the rows of a day or of the last N days

Standard library only. Nothing here talks to the network or to the broker.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = "/mnt/project-files/diario"
CONFIG = "trader/config.json"
STATUSES = ("planejado", "operado", "recusado", "nao_confirmado", "ignorado")
FLAT_BAR = "15:50"          # the last 5-minute bar (15:50-15:55): flat at its close
LESSONS_TEMPLATE = """# Licoes do diario de operacoes

Este arquivo e lido pelo Pre-mercado e pelo Radar antes de cada decisao
(`journal.py lessons`). Ha duas secoes, com pesos diferentes:

- **Regras aprovadas**: so entram com o "sim" explicito do Cristian, depois de
  uma revisao semanal. As rotinas aplicam estas regras.
- **Observacoes**: o que foi notado nas operacoes. Nao tem forca de regra:
  as rotinas apenas mencionam quando for relevante.

## Regras aprovadas

(nenhuma ainda)

## Observacoes

"""


class JournalError(Exception):
    pass


# ---------------------------------------------------------------- time (New York, no tz files)

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """-4 in US daylight time, -5 otherwise (second Sunday of March to first Sunday of November)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))
    return -4 if start <= utc.replace(tzinfo=None) < end else -5


def to_eastern(utc):
    utc = utc.replace(tzinfo=None)
    return utc + datetime.timedelta(hours=eastern_offset(utc))


def now_eastern():
    return to_eastern(datetime.datetime.now(datetime.timezone.utc))


def parse_utc(text):
    """'2026-11-03T16:02:13Z' (or with +00:00) -> naive UTC datetime."""
    t = str(text).strip().replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise JournalError("time %r is not ISO like 2026-11-03T16:02:13Z" % (text,))
    if d.tzinfo is not None:
        d = (d - d.utcoffset()).replace(tzinfo=None)
    return d


def hm(text):
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(text or "").strip())
    if not m:
        raise JournalError("time %r is not HH:MM (New York)" % (text,))
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt_hm(minutes):
    return "%02d:%02d" % divmod(int(minutes), 60)


def parse_date(text):
    try:
        return datetime.date.fromisoformat(str(text))
    except ValueError:
        raise JournalError("date %r is not YYYY-MM-DD" % (text,))


# ---------------------------------------------------------------- storage

def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def settings(cfg=None):
    cfg = load_config() if cfg is None else cfg
    j = dict(cfg.get("journal") or {})
    return {
        "dir": j.get("dir") or DEFAULT_DIR,
        "min_sample": int(j.get("min_sample", 30)),
        "lookback_days": int(j.get("lookback_days", 20)),
        "commission_round_trip": float(j.get("commission_round_trip", 2.0)),
        "min_risk_per_share": float(j.get("min_risk_per_share", 0.50)),
    }


def journal_dir(arg=None):
    d = arg or os.environ.get("TRADER_JOURNAL_DIR") or settings()["dir"]
    parent = os.path.dirname(os.path.abspath(d))
    if not os.path.isdir(parent):
        fallback = os.path.join("trader", "diario")
        sys.stderr.write("AVISO: %s nao existe nesta sessao; usando %s, que se perde quando a sessao "
                         "acabar. Copie para a pasta do projeto antes de sair.\n" % (parent, fallback))
        d = fallback
    os.makedirs(d, exist_ok=True)
    return d


def rows_path(d):
    return os.path.join(d, "diario.jsonl")


def load_rows(d):
    p = rows_path(d)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                raise JournalError("%s line %d is not JSON; fix it by hand before writing" % (p, i))
    return rows


def save_rows(d, rows):
    rows = sorted(rows, key=lambda r: (r.get("date", ""), r.get("first_seen", ""), r.get("id", "")))
    p = rows_path(d)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, p)


def lessons_path(d):
    p = os.path.join(d, "licoes.md")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(LESSONS_TEMPLATE)
    return p


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find(rows, rid=None, date=None, symbol=None):
    if rid:
        hit = [r for r in rows if r["id"] == rid]
    else:
        if not (date and symbol):
            raise JournalError("pass --id, or --date and --symbol")
        hit = [r for r in rows if r["date"] == date and r["symbol"] == symbol.upper()]
    if not hit:
        raise JournalError("no row for %s" % (rid or "%s %s" % (date, symbol)))
    if len(hit) > 1:
        raise JournalError("%d rows for %s %s (%s): pass --id" % (len(hit), date, symbol,
                                                                 ", ".join(r["id"] for r in hit)))
    return hit[0]


def new_id(rows, date, symbol):
    n = 1 + sum(1 for r in rows if r["date"] == date and r["symbol"] == symbol)
    rid = "%s-%s-%d" % (date, symbol, n)
    while any(r["id"] == rid for r in rows):
        n += 1
        rid = "%s-%s-%d" % (date, symbol, n)
    return rid


# ---------------------------------------------------------------- the numbers of one row

def plan_rr(p):
    try:
        risk = p["entry"] - p["stop"]
        return round((p["target"] - p["entry"]) / risk, 2) if risk > 0 else None
    except (KeyError, TypeError):
        return None


def result_of(r):
    """The outcome the learning uses: the real trade when closed, else the simulation. A row replaced
    by a re-made plan of the same breakout (superseded_by) counts once, through the new row."""
    if r.get("superseded_by"):
        return None
    real = r.get("real") or {}
    if real.get("status") == "fechado" and real.get("R") is not None:
        return dict(kind="real", R=real["R"], outcome=real.get("outcome"), net=real.get("net"))
    if real.get("status") == "aberto":
        return None
    sim = r.get("sim") or {}
    if sim.get("R") is not None:
        return dict(kind="sim", R=sim["R"], outcome=sim.get("outcome"), net=sim.get("net"))
    return None


def bucket(r, dim):
    p = r.get("plan") or {}
    if dim == "hora":
        t = hm(r["first_seen"])
        return "1) ate 10:59" if t < 660 else "2) 11:00-12:59" if t < 780 else "3) 13:00-14:29" if t < 870 else "4) 14:30+"
    if dim == "risco_acao":
        rs = round((p.get("entry") or 0) - (p.get("stop") or 0), 4)
        return "1) < 0,30" if rs < 0.30 else "2) 0,30-0,49" if rs < 0.50 else "3) >= 0,50"
    if dim == "rr_plano":
        rr = plan_rr(p)
        if rr is None:
            return "?"
        return "1) < 1,5" if rr < 1.5 else "2) 1,5-2,4" if rr < 2.5 else "3) >= 2,5"
    if dim == "preco":
        e = p.get("entry") or 0
        return "1) < 10" if e < 10 else "2) 10-49" if e < 50 else "3) >= 50"
    if dim == "atraso":
        if not r.get("signal_time"):
            return "?"
        lag = hm(r["first_seen"]) - hm(r["signal_time"])
        return "1) <= 15 min" if lag <= 15 else "2) 16-60 min" if lag <= 60 else "3) > 60 min"
    if dim == "dia_semana":
        return ["1) seg", "2) ter", "3) qua", "4) qui", "5) sex", "6) sab", "7) dom"][parse_date(r["date"]).weekday()]
    if dim == "setup":
        return r.get("setup") or "?"
    if dim == "maquina_tempo":
        tm = r.get("tm_mean_R")
        if tm is None:
            return "?"
        return "1) R medio <= 0" if tm <= 0 else "2) R medio > 0"
    if dim == "balanco":
        return "1) balanco/noticia" if "balanco" in (r.get("tags") or []) else "2) dia comum"
    if dim == "decisao":
        return (r.get("decision") or {}).get("status", "planejado")
    if dim == "fonte":
        return r.get("source") or "?"
    raise JournalError("unknown dimension %s" % dim)


DIMS = ("hora", "risco_acao", "rr_plano", "preco", "atraso", "dia_semana", "setup", "balanco", "maquina_tempo",
        "decisao", "fonte")
# The dimensions a rule change can be proposed on (decisao and fonte only describe the journal;
# maquina_tempo has its own pre-registered test in calibration()).
RULE_DIMS = ("hora", "risco_acao", "rr_plano", "preco", "atraso", "dia_semana", "setup", "balanco")


# ---------------------------------------------------------------- statistics (no numpy)

T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
       12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04, 40: 2.02, 60: 2.00, 120: 1.98}


def t95(df):
    if df <= 0:
        return float("nan")
    keys = sorted(T95)
    for k in keys:
        if df <= k:
            return T95[k]
    return 1.96


def mean_ci(xs):
    n = len(xs)
    if n == 0:
        return None
    m = sum(xs) / n
    if n < 2:
        return dict(n=n, mean=m, sd=None, lo=None, hi=None)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    h = t95(n - 1) * sd / math.sqrt(n)
    return dict(n=n, mean=m, sd=sd, lo=m - h, hi=m + h)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def welch_p(a, b):
    """Two-sided p of mean(a) != mean(b), normal approximation (fine when both n >= 30)."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return None
    return 2 * norm_sf(abs(ma - mb) / se)


def spearman(pairs):
    """Rank correlation of (x, y) pairs, ties averaged."""
    n = len(pairs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return out
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def calibration(rows, min_sample):
    """Does the time machine's mean R predict the result? Pre-registered 2026-11-06 (the rule
    "only trade with tm mean R > 0" was approved 2026-11-05 on 8 cases):
    with >= min_sample results on EACH side, the rule keeps the time machine only if the
    R > 0 side is better with p < 0.05; otherwise the review proposes dropping the time
    machine from the rule (the rule then only means fewer trades)."""
    pairs = []
    for r in rows:
        res = result_of(r)
        if res is None or r.get("tm_mean_R") is None:
            continue
        pairs.append((float(r["tm_mean_R"]), float(res["R"])))
    neg = [y for x, y in pairs if x <= 0]
    pos = [y for x, y in pairs if x > 0]
    out = dict(n=len(pairs), neg=mean_ci(neg), pos=mean_ci(pos), rho=spearman(pairs), p=None, verdict="amostra")
    if len(neg) >= min_sample and len(pos) >= min_sample:
        out["p"] = welch_p(pos, neg)
        better = sum(pos) / len(pos) > sum(neg) / len(neg)
        out["verdict"] = "preve" if (better and out["p"] is not None and out["p"] < 0.05) else "nao_preve"
    return out


def calibration_lines(cal, min_sample):
    def side(m, label):
        if not m:
            return "%s: 0 casos" % label
        ci = ("IC95 %s a %s" % (fnum(m["lo"]), fnum(m["hi"]))) if m.get("lo") is not None else "IC95 -"
        return "%s: %d casos, media %s R (%s)" % (label, m["n"], fnum(m["mean"]), ci)
    L = ["- %s" % side(cal["neg"], "R medio da maquina do tempo <= 0"),
         "- %s" % side(cal["pos"], "R medio > 0")]
    if cal["rho"] is not None:
        L.append("- Correlacao de postos (Spearman) entre o R medio previsto e o R obtido: %s (0 = nenhuma relacao, "
                 "1 = ordem perfeita), em %d planos." % (fnum(cal["rho"]), cal["n"]))
    nn = (cal["neg"] or {}).get("n", 0)
    pn = (cal["pos"] or {}).get("n", 0)
    if cal["verdict"] == "amostra":
        L.append("- **Ainda sem conclusao:** o criterio combinado pede %d casos de cada lado (faltam %d com R <= 0 e %d "
                 "com R > 0). A regra aprovada em 05/11 continua valendo enquanto isso." % (
                     min_sample, max(0, min_sample - nn), max(0, min_sample - pn)))
    elif cal["verdict"] == "preve":
        L.append("- **A maquina do tempo separa os planos** (p %.3f): a regra \"so operar com R medio > 0\" "
                 "tem base nos dados." % cal["p"])
    else:
        L.append("- **A maquina do tempo NAO separa os planos** (p %s): pelo criterio combinado em 06/11, propor ao "
                 "Cristian tirar a maquina do tempo da regra (a regra so estaria reduzindo trades ao acaso)." % (
                     ("%.3f" % cal["p"]) if cal["p"] is not None else "-"))
    return L


def group_stats(rows, dim):
    groups = {}
    for r in rows:
        res = result_of(r)
        if res is None:
            continue
        groups.setdefault(bucket(r, dim), []).append((r, res))
    out = []
    for key in sorted(groups):
        items = groups[key]
        rs = [res["R"] for _, res in items]
        wins = sum(1 for x in rs if x > 0)
        ci = mean_ci(rs)
        lo, hi = wilson(wins, len(rs))
        nets = [res["net"] for _, res in items if res.get("net") is not None]
        out.append(dict(key=key, n=len(rs), mean=ci["mean"], lo=ci["lo"], hi=ci["hi"], wins=wins,
                        win_lo=lo, win_hi=hi, net=sum(nets) if nets else None,
                        real=sum(1 for _, res in items if res["kind"] == "real"), rs=rs))
    return out


def fnum(x, nd=2, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    s = ("%+." if sign else "%.") + str(nd) + "f"
    return (s % x).replace(".", ",")


def fusd(x):
    if x is None:
        return "-"
    return ("-US$ %.2f" % -x if x < 0 else "+US$ %.2f" % x).replace(".", ",")


# ---------------------------------------------------------------- commands

def cmd_plan(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    date = a.date or now_eastern().date().isoformat()
    parse_date(date)
    sym = a.symbol.upper()
    seen = a.time or now_eastern().strftime("%H:%M")
    hm(seen)
    if a.signal_time:
        hm(a.signal_time)
    if not (a.stop < a.entry < a.target):
        raise JournalError("a long plan needs stop < entry < target (got %s / %s / %s)" % (a.stop, a.entry, a.target))
    same = [r for r in rows if r["date"] == date and r["symbol"] == sym
            and abs(r["plan"]["entry"] - a.entry) < 0.005 and abs(r["plan"]["stop"] - a.stop) < 0.005]
    if same:
        r = same[0]
        if seen not in r["seen_at"]:
            r["seen_at"] = sorted(set(r["seen_at"] + [seen]), key=hm)
        r["first_seen"] = r["seen_at"][0]
        verb = "updated"
    else:
        r = {"id": new_id(rows, date, sym), "date": date, "symbol": sym, "first_seen": seen, "seen_at": [seen],
             "created": stamp(), "decision": {"status": "planejado"}}
        rows.append(r)
        verb = "new"
    r["source"] = a.source
    r["setup"] = a.setup
    if a.signal_time:
        r["signal_time"] = a.signal_time
    r["plan"] = {"entry": a.entry, "stop": a.stop, "target": a.target, "shares": a.shares}
    r["plan"]["rr"] = plan_rr(r["plan"])
    r["plan"]["risk_per_share"] = round(a.entry - a.stop, 4)
    for key, val in (("risk_pass", a.risk), ("tm_mean_R", a.tm_mean_r), ("tm_p_target", a.tm_p_target),
                     ("tm_p_stop", a.tm_p_stop), ("data", a.data), ("balance", a.balance)):
        if val is not None:
            r[key] = val
    if a.tag:
        r["tags"] = sorted(set((r.get("tags") or []) + a.tag))
    if a.notes:
        r["notes"] = a.notes
    save_rows(d, rows)
    rs = r["plan"]["risk_per_share"]
    warn = ""
    if rs < settings()["min_risk_per_share"]:
        warn = "  (risco/acao %.2f < %.2f: a comissao come o ganho)" % (rs, settings()["min_risk_per_share"])
    print("%s %s: %s %s entrada %.2f stop %.2f alvo %.2f, R:R %s, visto %s%s" % (
        verb, r["id"], date, sym, a.entry, a.stop, a.target, r["plan"]["rr"], ", ".join(r["seen_at"]), warn))


def cmd_decide(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    r = find(rows, a.id, a.date, a.symbol)
    r["decision"] = {"status": a.status, "reason": a.reason, "at": stamp()}
    if a.superseded_by:
        find(rows, a.superseded_by)
        r["superseded_by"] = a.superseded_by
    save_rows(d, rows)
    print("%s: %s (%s)" % (r["id"], a.status, a.reason or "sem motivo"))


def _fills_of(doc):
    trades = doc.get("trades") if isinstance(doc, dict) else doc
    if not isinstance(trades, list):
        raise JournalError("not a get_account_trades answer (no 'trades' list)")
    fills = []
    for t in trades:
        if t.get("sec_type", "STK") != "STK":
            continue
        try:
            fills.append({"trade_id": str(t["trade_id"]), "symbol": str(t["symbol"]).upper(),
                          "side": str(t["side"]).upper(), "size": float(t["size"]), "price": float(t["price"]),
                          "commission": float(t.get("commission") or 0), "time": t["trade_time"],
                          "order_id": t.get("order_id"), "order_type": t.get("order_type")})
        except (KeyError, TypeError, ValueError):
            raise JournalError("trade %r lacks trade_id, symbol, side, size, price or trade_time" % (t,))
    fills.sort(key=lambda f: (parse_utc(f["time"]), f["side"] != "BUY"))
    return fills


def _recompute(r):
    real = r["real"]
    fills = real["fills"]
    buys = [f for f in fills if f["side"] == "BUY"]
    sells = [f for f in fills if f["side"] == "SELL"]
    bq, sq = sum(f["size"] for f in buys), sum(f["size"] for f in sells)
    comm = round(sum(f["commission"] for f in fills), 2)
    real["shares"] = bq
    real["commission"] = comm
    if bq:
        real["entry_price"] = round(sum(f["size"] * f["price"] for f in buys) / bq, 4)
        real["entry_time"] = to_eastern(parse_utc(buys[0]["time"])).strftime("%H:%M")
    if bq and abs(bq - sq) < 1e-9:
        xp = sum(f["size"] * f["price"] for f in sells) / sq
        last = max(sells, key=lambda f: parse_utc(f["time"]))
        real["status"] = "fechado"
        real["exit_price"] = round(xp, 4)
        real["exit_time"] = to_eastern(parse_utc(last["time"])).strftime("%H:%M")
        real["exit_date"] = to_eastern(parse_utc(last["time"])).date().isoformat()
        gross = sum(f["size"] * f["price"] for f in sells) - sum(f["size"] * f["price"] for f in buys)
        real["gross"] = round(gross, 2)
        real["net"] = round(gross - sum(f["commission"] for f in fills), 2)
        p = r.get("plan") or {}
        stop, tgt = p.get("stop"), p.get("target")
        if any((f.get("order_type") or "").upper().startswith("STOP") for f in sells):
            real["outcome"] = "stop"
        elif tgt and xp >= tgt - 0.01:
            real["outcome"] = "alvo"
        elif real["exit_date"] != r["date"]:
            real["outcome"] = "levou para outro dia"
        else:
            real["outcome"] = "zerou"
        risk = real["entry_price"] - stop if stop else None
        real["R"] = round((xp - real["entry_price"]) / risk, 3) if risk and risk > 0 else None
    else:
        real["status"] = "aberto"
        real["open_shares"] = bq - sq
        for k in ("exit_price", "exit_time", "exit_date", "gross", "net", "outcome", "R"):
            real.pop(k, None)


def cmd_ibkr_trades(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    try:
        with open(a.inp, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise JournalError("could not read %s: %s" % (a.inp, e))
    known = {f["trade_id"] for r in rows for f in (r.get("real") or {}).get("fills", [])}
    touched, created, skipped = [], [], 0
    for f in _fills_of(doc):
        if f["trade_id"] in known:
            skipped += 1
            continue
        et = to_eastern(parse_utc(f["time"]))
        day, sym = et.date().isoformat(), f["symbol"]
        open_rows = [r for r in rows if r["symbol"] == sym and (r.get("real") or {}).get("status") == "aberto"]
        if open_rows:
            r = open_rows[-1]
        elif f["side"] == "BUY":
            cands = [r for r in rows if r["date"] == day and r["symbol"] == sym and not r.get("real")
                     and not r.get("superseded_by")]
            before = [r for r in cands if hm(r["first_seen"]) <= et.hour * 60 + et.minute]
            pick = before or cands
            pick.sort(key=lambda r: ((r.get("decision") or {}).get("status") != "operado", -hm(r["first_seen"])))
            if pick:
                r = pick[0]
            else:
                r = {"id": new_id(rows, day, sym), "date": day, "symbol": sym, "first_seen": et.strftime("%H:%M"),
                     "seen_at": [et.strftime("%H:%M")], "source": "manual", "setup": "?", "created": stamp(),
                     "notes": "compra na IBKR sem plano registrado no diario"}
                rows.append(r)
                created.append(r["id"])
        else:
            r = {"id": new_id(rows, day, sym), "date": day, "symbol": sym, "first_seen": et.strftime("%H:%M"),
                 "seen_at": [et.strftime("%H:%M")], "source": "manual", "setup": "?", "created": stamp(),
                 "notes": "venda na IBKR sem compra no diario (a compra ficou fora da janela importada?)"}
            rows.append(r)
            created.append(r["id"])
        r.setdefault("real", {"fills": []})
        r["real"]["fills"].append(f)
        r["real"]["source"] = "ibkr get_account_trades"
        r["decision"] = dict(r.get("decision") or {}, status="operado")
        r["decision"].setdefault("at", stamp())
        _recompute(r)
        known.add(f["trade_id"])
        if r["id"] not in touched:
            touched.append(r["id"])
    save_rows(d, rows)
    print("IBKR: %d fills already in the journal, %d rows updated%s." % (
        skipped, len(touched), (", %d new rows without a plan (%s)" % (len(created), ", ".join(created))) if created else ""))
    for rid in touched:
        r = find(rows, rid)
        real = r["real"]
        if real["status"] == "fechado":
            print("  %s: %g acoes %.3f -> %.3f (%s), bruto %s, comissao %s, liquido %s, %s R" % (
                rid, real["shares"], real["entry_price"], real["exit_price"], real["outcome"],
                fusd(real["gross"]), fusd(-real["commission"]).replace("+", ""), fusd(real["net"]), fnum(real.get("R"))))
        else:
            print("  %s: ABERTO, %g acoes a %.2f desde %s %s" % (rid, real["open_shares"], real.get("entry_price", 0),
                                                                r["date"], real.get("entry_time", "")))


def _load_bars(path, day):
    """Yahoo v8 chart JSON (what ibkr_chart.py bars writes) -> {minute_of_day: bar} for one New York day."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        res = doc["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        raise JournalError("could not read bars %s: %s" % (path, e))
    out = {}
    for i, ts in enumerate(res.get("timestamp") or []):
        et = to_eastern(datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc))
        if et.date() != day:
            continue
        vals = [q[k][i] for k in ("open", "high", "low", "close")]
        if any(v is None for v in vals):
            continue
        out[et.hour * 60 + et.minute] = dict(zip("ohlc", map(float, vals)))
    return out, (res.get("meta") or {}).get("source", "yahoo")


def simulate_one(r, bars, commission):
    """Market order at the open of the first 5-minute bar after the plan was posted; stop first when a
    bar touches both (conservative); flat at the close of the 15:50 bar."""
    p = r["plan"]
    post = hm(r["first_seen"])
    start = post - (post - 570) % 5 + 5
    flat = hm(FLAT_BAR)
    if start > flat:
        return {"skip": "aviso depois da ultima barra"}
    if start not in bars:
        return {"skip": "sem a barra das %s" % fmt_hm(start)}
    fill = bars[start]["o"]
    stop, tgt = p["stop"], p["target"]
    if fill <= stop:
        return {"skip": "preco ja abaixo do stop", "fill_price": fill}
    if fill >= tgt:
        return {"skip": "preco ja no/acima do alvo", "fill_price": fill}
    if (tgt - fill) / (fill - stop) < 1.0:
        return {"skip": "retorno/risco < 1 no preco de entrada (o risk bloqueia)", "fill_price": fill}
    exit_px = outcome = exit_t = None
    ambiguous = False
    for t in range(start, flat + 1, 5):
        b = bars.get(t)
        if not b:
            continue
        if t != start and b["o"] <= stop:
            exit_px, outcome, exit_t = b["o"], "stop (gap)", t
            break
        hit_s, hit_t = b["l"] <= stop, b["h"] >= tgt
        if hit_s and hit_t:
            ambiguous = True
        if hit_s:
            exit_px, outcome, exit_t = stop, "stop", t
            break
        if hit_t:
            exit_px, outcome, exit_t = tgt, "alvo", t
            break
    if exit_px is None:
        last = max(t for t in bars if t <= flat)
        if last < flat:
            return {"skip": "barras incompletas (ultima %s): pregao ainda aberto?" % fmt_hm(last)}
        exit_px, outcome, exit_t = bars[flat]["c"], "zerou 15:55", flat
    risk = fill - stop
    out = {"fill_time": fmt_hm(start), "fill_price": round(fill, 4), "exit_price": round(exit_px, 4),
           "exit_time": fmt_hm(exit_t + 5), "outcome": outcome, "R": round((exit_px - fill) / risk, 3),
           "ambiguous_bar": ambiguous}
    n = p.get("shares")
    if n:
        out["gross"] = round(n * (exit_px - fill), 2)
        out["net"] = round(out["gross"] - commission, 2)
    return out


def cmd_simulate(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    day = parse_date(a.date)
    commission = settings()["commission_round_trip"] if a.commission is None else a.commission
    todo = [r for r in rows if r["date"] == a.date and not (r.get("real") or {}).get("status") == "fechado"
            and (a.redo or not r.get("sim"))]
    if not todo:
        print("Nada a simular em %s (sem planos, ou ja simulados: use --redo)." % a.date)
        return
    done = 0
    for r in todo:
        path = os.path.join(a.bars, "%s.json" % r["symbol"].replace(".", "-"))
        if not os.path.exists(path):
            print("  %s: sem barras em %s" % (r["id"], path))
            continue
        bars, src = _load_bars(path, day)
        if not bars:
            print("  %s: o arquivo nao tem barras de %s" % (r["id"], a.date))
            continue
        res = simulate_one(r, bars, commission)
        res.update(method="mercado na barra seguinte ao aviso; stop antes do alvo na mesma barra; zera 15:55",
                   bars=src, commission_assumed=commission, at=stamp())
        r["sim"] = res
        done += 1
        if res.get("skip"):
            print("  %s: nao entraria (%s)" % (r["id"], res["skip"]))
        else:
            print("  %s: %s %.2f -> %s %.2f %s, %s R%s" % (
                r["id"], res["fill_time"], res["fill_price"], res["exit_time"], res["exit_price"], res["outcome"],
                fnum(res["R"]), (", liquido %s com %d acoes" % (fusd(res["net"]), r["plan"]["shares"])) if "net" in res else ""))
    save_rows(d, rows)
    print("Simulados %d de %d planos de %s (barras: %s). Resultado simulado, nao real." % (done, len(todo), a.date, a.bars))


def cmd_note(a):
    d = journal_dir(a.dir)
    p = lessons_path(d)
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    day = now_eastern().date().isoformat()
    if a.rule:
        if not a.approved_by:
            raise JournalError("an approved rule needs --approved-by (who said yes, and where)")
        line = "- **%s** %s (aprovada por %s)\n" % (day, a.text.strip(), a.approved_by)
        head = "## Regras aprovadas"
        text = text.replace("(nenhuma ainda)\n", "")
        i = text.find(head)
        j = text.find("\n## ", i + len(head))
        if i < 0 or j < 0:
            raise JournalError("%s lost its '## Regras aprovadas' / '## Observacoes' headings" % p)
        body = text[i + len(head):j].strip("\n")
        text = text[:i + len(head)] + "\n\n" + (body + "\n" if body else "") + line + text[j:]
    else:
        text = text.rstrip("\n") + "\n- **%s** [%s] %s\n" % (day, a.kind, a.text.strip())
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("%s -> %s" % ("regra aprovada" if a.rule else "observacao", p))


def _section(text, head):
    i = text.find(head)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(head))
    return text[i + len(head): j if j >= 0 else len(text)].strip()


def _summary_line(rows):
    res = [result_of(r) for r in rows]
    res = [x for x in res if x]
    if not res:
        return "sem resultado ainda"
    rs = [x["R"] for x in res]
    ci = mean_ci(rs)
    wins = sum(1 for x in rs if x > 0)
    nets = [x["net"] for x in res if x.get("net") is not None]
    money = ""
    if nets:
        money = "; com a comissao: liquido %s em %d trades (%s por trade), %d%% no positivo" % (
            fusd(sum(nets)), len(nets), fusd(sum(nets) / len(nets)), round(100 * sum(1 for x in nets if x > 0) / len(nets)))
    return "%d com resultado (%d reais, %d simulados): media %s R por trade (IC95 %s a %s), %d%% positivos antes da comissao%s" % (
        len(rs), sum(1 for x in res if x["kind"] == "real"), sum(1 for x in res if x["kind"] == "sim"),
        fnum(ci["mean"]), fnum(ci["lo"]), fnum(ci["hi"]), round(100 * wins / len(rs)), money)


def cmd_lessons(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    st = settings()
    days = a.days or st["lookback_days"]
    today = now_eastern().date()
    p = lessons_path(d)
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    print("DIARIO DE OPERACOES (%s, %d linhas)" % (d, len(rows)))
    print()
    print("Regras aprovadas (aplicar):")
    rules = _section(text, "## Regras aprovadas")
    print("  " + (rules.replace("\n", "\n  ") if rules else "(nenhuma)"))
    obs = [l for l in _section(text, "## Observacoes").splitlines() if l.startswith("- ")]
    print()
    print("Observacoes recentes (contexto, nao sao regras):")
    for l in obs[-a.notes:] or ["(nenhuma)"]:
        print("  " + l)
    print()
    opened = [r for r in rows if (r.get("real") or {}).get("status") == "aberto"]
    if opened:
        print("POSICOES ABERTAS NO DIARIO:")
        for r in opened:
            real = r["real"]
            late = " (DE OUTRO DIA: a regra e zerar ate 15:55)" if r["date"] != today.isoformat() else ""
            print("  %s: %g acoes a %.2f, stop %s alvo %s%s" % (r["id"], real["open_shares"], real.get("entry_price", 0),
                                                              (r.get("plan") or {}).get("stop"), (r.get("plan") or {}).get("target"), late))
        print()
    pend = [r for r in rows if parse_date(r["date"]) < today and not result_of(r) and not r.get("superseded_by")
            and not (r.get("sim") or {}).get("skip") and not r.get("real")]
    if pend:
        ds = sorted({r["date"] for r in pend})
        print("Planos de dias passados sem resultado: %d (%s). Rode `journal.py simulate` com as barras desses dias." % (
            len(pend), ", ".join(ds[-5:])))
        print()
    recent = [r for r in rows if (today - parse_date(r["date"])).days <= days * 7 // 5 + 2]
    print("Ultimos %d pregoes: %d planos; %s." % (days, len(recent), _summary_line(recent)))
    real = [r for r in rows if (r.get("real") or {}).get("status") == "fechado"]
    if real:
        net = sum(r["real"]["net"] for r in real)
        wins = sum(1 for r in real if r["real"]["net"] > 0)
        print("Trades reais fechados (todos): %d, %d no positivo, liquido %s (com comissao)." % (len(real), wins, fusd(net)))
    print("Tudo: %d planos; %s." % (len(rows), _summary_line(rows)))
    print()
    print("Por faixa (todas as linhas com resultado; n < %d = amostra insuficiente, nao decida por isso):" % st["min_sample"])
    for dim in ("risco_acao", "atraso", "hora"):
        parts = []
        for g in group_stats(rows, dim):
            flag = "" if g["n"] >= st["min_sample"] else "*"
            parts.append("%s: n=%d%s %s R" % (g["key"].split(") ", 1)[-1], g["n"], flag, fnum(g["mean"])))
        print("  %-11s %s" % (dim, "; ".join(parts) or "-"))
    print("  (* = menos de %d casos; R = resultado em multiplos do risco, sem comissao)" % st["min_sample"])


def _rows_window(rows, start, end):
    return [r for r in rows if start <= parse_date(r["date"]) <= end]


def cmd_stats(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    if a.since:
        rows = [r for r in rows if r["date"] >= a.since]
    st = settings()
    print("%d planos; %s." % (len(rows), _summary_line(rows)))
    for dim in (a.by or DIMS):
        print()
        print("Por %s:" % dim)
        print("  %-16s %4s %8s %17s %6s %15s %11s" % ("faixa", "n", "media R", "IC95 da media", "acerto", "IC95 acerto", "liquido $"))
        for g in group_stats(rows, dim):
            print("  %-16s %4d %8s %8s a %6s %5d%% %6s a %5s %11s%s" % (
                g["key"], g["n"], fnum(g["mean"]), fnum(g["lo"]), fnum(g["hi"]), round(100 * g["wins"] / g["n"]),
                ("%d%%" % round(100 * g["win_lo"])) if g["win_lo"] is not None else "-",
                ("%d%%" % round(100 * g["win_hi"])) if g["win_hi"] is not None else "-",
                fusd(g["net"]), "" if g["n"] >= st["min_sample"] else "  amostra insuficiente"))


def proposals(rows, min_sample):
    """Buckets whose result differs from the rest with enough sample, Bonferroni over every test run."""
    tests = []
    for dim in RULE_DIMS:
        groups = group_stats(rows, dim)
        if len(groups) < 2:
            continue
        for g in groups:
            rest = [x for h in groups if h["key"] != g["key"] for x in h["rs"]]
            if g["n"] < min_sample or len(rest) < min_sample:
                continue
            tests.append((dim, g, rest, welch_p(g["rs"], rest)))
    k = max(1, len(tests))
    out = []
    for dim, g, rest, p in tests:
        if p is None or p * k >= 0.05:
            continue
        mr = sum(rest) / len(rest)
        if g["hi"] is not None and g["hi"] < 0:
            kind = "evitar"
        elif g["lo"] is not None and g["lo"] > 0:
            kind = "priorizar"
        else:
            kind = "diferente, mas o IC da faixa cruza zero"
        out.append(dict(dim=dim, key=g["key"], n=g["n"], mean=g["mean"], lo=g["lo"], hi=g["hi"], rest_n=len(rest),
                        rest_mean=mr, p=p, p_adj=min(1.0, p * k), kind=kind))
    return out, k


def cmd_review(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    st = settings()
    today = now_eastern().date()
    if a.week:
        m = re.match(r"^(\d{4})-W(\d{2})$", a.week)
        if not m:
            raise JournalError("--week is like 2026-W45")
        monday = datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    else:
        monday = today - datetime.timedelta(days=today.weekday())
        if today.weekday() < 4:            # before Friday: review the week that ended
            monday -= datetime.timedelta(days=7)
    friday = monday + datetime.timedelta(days=4)
    iso = monday.isocalendar()
    tag = "%d-W%02d" % (iso[0], iso[1])
    week = _rows_window(rows, monday, friday)
    L = []
    L.append("# Revisao semanal %s (%s a %s)" % (tag, monday.strftime("%d/%m"), friday.strftime("%d/%m/%Y")))
    L.append("")
    L.append("Gerada por `journal.py review` em %s (Nova York). Os numeros vem do diario em `%s`." % (
        now_eastern().strftime("%Y-%m-%d %H:%M"), rows_path(d)))
    L.append("**Nada aqui muda uma regra sozinho.** As propostas so valem depois do \"sim\" do Cristian.")
    L.append("")
    L.append("## Como ler")
    L.append("")
    L.append("- **R** e o resultado em multiplos do risco do plano: +1 R = ganhou o que arriscou; -1 R = bateu o stop. "
             "O R e calculado sem comissao, para comparar trades de tamanhos diferentes. O liquido em dolar ja desconta a comissao.")
    L.append("- **Real** = trade feito na IBKR (preco de execucao e comissao da corretora). **Simulado** = plano nao operado, "
             "testado nas barras de 5 min (entrada a mercado na barra seguinte ao aviso, stop antes do alvo quando a mesma barra toca os dois, zera 15:55).")
    L.append("- O liquido em dolar depende do tamanho de cada plano: ate 28/10 o orcamento era fixo em US$ 200; depois, o saldo da IBKR. "
             "Nos simulados, a comissao e a atual da IBKR (US$ %.2f ida e volta), mesmo nos dias antes da IBKR. "
             "Por isso a comparacao justa entre faixas e o R; o dolar mostra o efeito da comissao." % st["commission_round_trip"])
    L.append("- **IC95** e a faixa onde a media verdadeira provavelmente esta. Se a faixa cruza o zero, nao da para dizer que ganha ou perde.")
    L.append("- Uma faixa so vira proposta com pelo menos %d casos **e** diferenca estatistica que sobrevive a correcao de Bonferroni "
             "(quanto mais faixas testamos, mais facil achar uma por acaso; a correcao compensa isso)." % st["min_sample"])
    L.append("")
    L.append("## A semana")
    L.append("")
    L.append("- Planos registrados: %d; %s." % (len(week), _summary_line(week)))
    dec = {}
    for r in week:
        dec.setdefault((r.get("decision") or {}).get("status", "planejado"), []).append(r)
    for s in STATUSES:
        if s in dec:
            L.append("- %s: %d" % (s, len(dec[s])))
    real = [r for r in week if r.get("real")]
    if real:
        L.append("")
        L.append("| Trade real | Acoes | Entrada | Saida | Resultado | Liquido | R |")
        L.append("|---|---|---|---|---|---|---|")
        for r in real:
            x = r["real"]
            if x["status"] == "fechado":
                L.append("| %s %s %s | %g | %.2f (%s) | %.2f (%s %s) | %s | %s | %s |" % (
                    r["date"], r["symbol"], r["first_seen"], x["shares"], x["entry_price"], x.get("entry_time"),
                    x["exit_price"], x.get("exit_date", ""), x.get("exit_time"), x["outcome"], fusd(x["net"]), fnum(x.get("R"))))
            else:
                L.append("| %s %s | %g | %.2f | aberto | - | - | - |" % (r["date"], r["symbol"], x["open_shares"], x.get("entry_price", 0)))
    declined = [r for r in week if (r.get("decision") or {}).get("status") in ("recusado", "nao_confirmado") and result_of(r)]
    if declined:
        L.append("")
        L.append("Planos que o Cristian recusou ou nao confirmou, e o que teria acontecido (simulado):")
        L.append("")
        for r in declined:
            res = result_of(r)
            L.append("- %s %s (%s): %s, %s R%s" % (r["date"], r["symbol"], (r.get("decision") or {}).get("reason") or "sem motivo",
                                                  res["outcome"], fnum(res["R"]), (", %s" % fusd(res["net"])) if res.get("net") is not None else ""))
    L.append("")
    L.append("## Todo o historico, por faixa")
    L.append("")
    L.append("Total: %d planos; %s." % (len(rows), _summary_line(rows)))
    for dim in RULE_DIMS:
        gs = group_stats(rows, dim)
        if not gs:
            continue
        L.append("")
        L.append("**%s**" % dim)
        L.append("")
        L.append("| Faixa | n | Media R | IC95 | Acerto | Liquido |")
        L.append("|---|---|---|---|---|---|")
        for g in gs:
            L.append("| %s | %d%s | %s | %s a %s | %d%% | %s |" % (
                g["key"], g["n"], "" if g["n"] >= st["min_sample"] else " (pouco)", fnum(g["mean"]), fnum(g["lo"]),
                fnum(g["hi"]), round(100 * g["wins"] / g["n"]), fusd(g["net"])))
    L.append("")
    L.append("## A maquina do tempo preve o resultado?")
    L.append("")
    L.extend(calibration_lines(calibration(rows, st["min_sample"]), st["min_sample"]))
    L.append("")
    L.append("## Propostas de mudanca de regra")
    L.append("")
    props, k = proposals(rows, st["min_sample"])
    if not props:
        tested = len([1 for dim in RULE_DIMS for g in group_stats(rows, dim)
                      if g["n"] >= st["min_sample"] and len(group_stats(rows, dim)) > 1])
        L.append("**Nenhuma.** Nenhuma faixa tem ao mesmo tempo %d casos ou mais (de cada lado da comparacao) e uma diferenca "
                 "que resista a correcao (%d faixas tinham amostra para testar). Mudar a regra agora seria ajustar a estrategia "
                 "ao acaso (overfitting)." % (st["min_sample"], tested))
    else:
        for i, pr in enumerate(props, 1):
            L.append("%d. **%s %s = %s**: %d casos, media %s R (IC95 %s a %s) contra %s R nas outras %d; p ajustado %.3f. "
                     "Sugestao: %s. Para valer, o Cristian responde \"sim, proposta %d\" e a regra entra em `licoes.md` "
                     "(e, se mudar um script ou o config, num PR)." % (
                         i, pr["kind"].upper(), pr["dim"], pr["key"], pr["n"], fnum(pr["mean"]), fnum(pr["lo"]), fnum(pr["hi"]),
                         fnum(pr["rest_mean"]), pr["rest_n"], pr["p_adj"],
                         "evitar planos nesta faixa" if pr["kind"] == "evitar" else
                         "dar preferencia a planos nesta faixa" if pr["kind"] == "priorizar" else "observar mais uma semana", i))
    L.append("")
    L.append("Observacoes de processo (o que deu errado na execucao, nao na estrategia) podem virar regra sem amostra estatistica, "
             "porque nao dependem de sorte: estao em `licoes.md` e a revisao lista as da semana abaixo.")
    L.append("")
    with open(lessons_path(d), encoding="utf-8") as fh:
        obs = [l for l in _section(fh.read(), "## Observacoes").splitlines() if l.startswith("- ")]
    wk = [l for l in obs if any(("**%s**" % (monday + datetime.timedelta(days=i)).isoformat()) in l for i in range(7))]
    L.append("## Observacoes da semana")
    L.append("")
    L.extend(wk or ["(nenhuma registrada)"])
    L.append("")
    out_dir = os.path.join(d, "revisoes")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "revisao-%s.md" % tag)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print()
    print("-> %s" % path)


def cmd_show(a):
    d = journal_dir(a.dir)
    rows = load_rows(d)
    if a.date:
        rows = [r for r in rows if r["date"] == a.date]
    else:
        today = now_eastern().date()
        rows = [r for r in rows if (today - parse_date(r["date"])).days <= a.days]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        p = r.get("plan") or {}
        res = result_of(r)
        sim = r.get("sim") or {}
        print("%-22s %s %-5s %7.2f/%7.2f/%7.2f x%-3s %-14s %s" % (
            r["id"], r["first_seen"], r.get("setup", "?")[:5], p.get("entry", 0), p.get("stop", 0), p.get("target", 0),
            p.get("shares") or "-", (r.get("decision") or {}).get("status", "planejado"),
            ("%s %s %s R" % (res["kind"], res["outcome"], fnum(res["R"]))) if res else
            ("nao entraria: %s" % sim["skip"]) if sim.get("skip") else
            ("ABERTO" if (r.get("real") or {}).get("status") == "aberto" else "-")))
    print("%d linhas" % len(rows))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="journal folder (default: journal.dir in trader/config.json, else %s)" % DEFAULT_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="record or update a plan")
    p.add_argument("--symbol", required=True)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float, required=True)
    p.add_argument("--shares", type=int)
    p.add_argument("--date", help="New York date (default today)")
    p.add_argument("--time", help="HH:MM New York when the plan was shown (default now)")
    p.add_argument("--signal-time", help="HH:MM of the breakout bar the plan comes from")
    p.add_argument("--source", default="radar", choices=("radar", "premercado", "manual", "backfill"))
    p.add_argument("--setup", default="orb")
    p.add_argument("--risk", choices=("PASS", "FAIL"), help="trader-risk verdict")
    p.add_argument("--tm-mean-r", type=float, help="time machine mean R")
    p.add_argument("--tm-p-target", type=float, help="time machine share that hit the target (0-1)")
    p.add_argument("--tm-p-stop", type=float, help="time machine share that hit the stop (0-1)")
    p.add_argument("--balance", type=float, help="IBKR balance used to size it")
    p.add_argument("--data", choices=("ibkr", "yahoo"), help="where the bars came from")
    p.add_argument("--tag", action="append", help="free tag, repeatable (earnings, gap, spy-down...)")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("decide", help="record the decision on a plan")
    p.add_argument("--id")
    p.add_argument("--date")
    p.add_argument("--symbol")
    p.add_argument("--status", required=True, choices=STATUSES)
    p.add_argument("--reason")
    p.add_argument("--superseded-by", help="id of the re-made plan of the same breakout (this row stops counting)")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("ibkr-trades", help="import a raw get_account_trades answer")
    p.add_argument("--in", dest="inp", required=True)
    p.set_defaults(func=cmd_ibkr_trades)

    p = sub.add_parser("simulate", help="resolve one day's plans on 5-minute bars")
    p.add_argument("--date", required=True)
    p.add_argument("--bars", required=True, help="folder with <SYM>.json (ibkr_chart.py bars --out)")
    p.add_argument("--commission", type=float, help="round-trip commission in US$ (default journal.commission_round_trip)")
    p.add_argument("--redo", action="store_true", help="simulate again rows that already have a simulation")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("note", help="add an observation or an approved rule to licoes.md")
    p.add_argument("--text", required=True)
    p.add_argument("--kind", default="processo", choices=("processo", "mercado", "estrategia", "dados"))
    p.add_argument("--rule", action="store_true", help="an APPROVED rule (needs --approved-by)")
    p.add_argument("--approved-by")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("lessons", help="the briefing to read before deciding")
    p.add_argument("--days", type=int)
    p.add_argument("--notes", type=int, default=8, help="how many recent observations to show")
    p.set_defaults(func=cmd_lessons)

    p = sub.add_parser("stats", help="results by bucket")
    p.add_argument("--by", action="append", choices=DIMS)
    p.add_argument("--since", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("review", help="weekly review -> revisoes/revisao-YYYY-Www.md")
    p.add_argument("--week", help="ISO week like 2026-W45 (default: this week from Friday on, else the last one)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("show", help="list rows")
    p.add_argument("--date")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except JournalError as e:
        print("journal: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Swing-trade watchlist scanner and planner for US and Indian (NSE/BSE) stocks.

Standard library only. Reads free Yahoo Finance daily candles (v8 chart
endpoint), builds weekly candles from them, and checks each stock on the
watchlist for a long swing setup: a breakout, a tight base just under a
breakout level, a bull flag, a pullback to the 20-day EMA or the 50-day
average in an uptrend, or an oversold dip above the 200-day average.

Everything lives in a `swing/` folder in the current directory:

  swing/watchlist.json   the tickers you watch            (add / remove / list)
  swing/config.json      account size per currency, risk  (config)
  swing/scan.json        the last scan                    (scan)
  swing/plans.json       the trade plans you made         (plan --write / review)

It never places an order. A setup is a chart that matched a rule, not a
prediction and not advice.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; swing-trade-skill/1.0)"
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
PAUSE = 0.3          # seconds between requests: be polite to a free endpoint
BACKOFF = 5.0        # seconds before the one retry after an HTTP 429
DIR = "swing"
DEFAULT_CONFIG = {
    "accounts": {"INR": 500000, "USD": 5000},
    "risk_pct": 1.0,            # percent of the account lost if the stop fills
    "max_position_pct": 25.0,   # no single position above this share of the account
    "trigger_days": 3,          # a buy-stop not hit in this many sessions is cancelled
    "time_stop_days": 15,       # out if it has not reached +1R in this many sessions
}
SYMBOLS = {"INR": "Rs ", "USD": "$"}
PRIORITY = ["breakout", "bull_flag", "base", "pullback_ema20", "pullback_sma50", "oversold_dip"]
LABELS = {
    "breakout": "Breakout: closed above its 55-day high on heavy volume",
    "bull_flag": "Bull flag: a sharp run, then a tight pause near the high",
    "base": "Tight base just under its 55-day high",
    "pullback_ema20": "Pullback to the 20-day EMA after a run up",
    "pullback_sma50": "Pullback to the 50-day average after a run up",
    "oversold_dip": "Oversold dip (RSI under 35) above the 200-day average",
}


# ----------------------------------------------------------------- files

def path(name):
    return os.path.join(DIR, name)


def load(name, default):
    try:
        with open(path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        sys.exit("swing.py: could not read %s: %s" % (path(name), e))


def save(name, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = path(name) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path(name))


def config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load("config.json", {}))
    return cfg


def norm(sym):
    """'nse:reliance' -> RELIANCE.NS, 'BSE:500325' -> 500325.BO, 'aapl' -> AAPL."""
    s = sym.strip().upper()
    if ":" in s:
        ex, s = s.split(":", 1)
        if ex in ("NSE", "BSE"):
            s = s.replace("_", "-") + (".NS" if ex == "NSE" else ".BO")   # TradingView NAM_INDIA = Yahoo NAM-INDIA
    return s


def split_tickers(items, file=None):
    """Tickers from arguments and/or a file, split on commas, spaces and new lines
    (a TradingView watchlist export is one comma-separated line)."""
    raw = list(items or [])
    if file:
        with open(file) as f:
            raw.append(f.read())
    out = []
    for item in raw:
        for t in item.replace(",", " ").split():
            if t.startswith("###"):
                continue        # a TradingView section header
            n = norm(t)
            if n and n not in out:
                out.append(n)
    return out


# ----------------------------------------------------------------- data

def fetch(sym, offline=None):
    """Daily candles for about two years: (meta, bars). bars are dicts."""
    if offline:
        with open(os.path.join(offline, sym + ".json")) as f:
            raw = json.load(f)
    else:
        raw, err = None, "no answer"
        q = urllib.parse.urlencode({"range": "2y", "interval": "1d", "includePrePost": "false"})
        for host in HOSTS:
            url = "%s/v8/finance/chart/%s?%s" % (host, urllib.parse.quote(sym), q)
            for attempt in (1, 2):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        raw = json.load(r)
                    break
                except urllib.error.HTTPError as e:
                    err = "HTTP %d" % e.code
                    if e.code == 404:
                        try:
                            raw = json.load(e)
                        except ValueError:
                            pass
                        break
                    if e.code == 429 and attempt == 1:
                        time.sleep(BACKOFF)
                        continue
                    break
                except (urllib.error.URLError, OSError, ValueError) as e:
                    err = str(getattr(e, "reason", e))
                    break
            if raw is not None:
                break
        time.sleep(PAUSE)
        if raw is None:
            raise RuntimeError("could not reach Yahoo (%s)" % err)
    chart = raw.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        msg = (chart.get("error") or {}).get("description") or "no data"
        raise RuntimeError("Yahoo has no data for %s (%s). Check the ticker: NSE needs .NS, BSE needs "
                           "the symbol plus .BO (RELIANCE.BO, not 500325.BO), US has no suffix." % (sym, msg))
    res = chart["result"][0]
    meta = res.get("meta") or {}
    q = (res.get("indicators") or {}).get("quote") or [{}]
    q = q[0]
    off = meta.get("gmtoffset") or 0
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            v = q["volume"][i] or 0
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c):
            continue           # a holiday or a gap in Yahoo's data
        day = dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).date()
        if bars and bars[-1]["date"] == day.isoformat():
            bars.pop()          # keep the last row Yahoo gives for a day
        bars.append({"ts": ts, "date": day.isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v})
    return meta, bars


def drop_live_bar(meta, bars, now_ts):
    """Drop today's bar while the session is still open: it is not a close yet."""
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = reg.get("start"), reg.get("end")
    if bars and start and end and start <= now_ts < end and bars[-1]["ts"] >= start:
        return bars[:-1], True
    return bars, False


# ----------------------------------------------------------------- indicators

def sma(xs, n, i=None):
    i = len(xs) - 1 if i is None else i
    if i + 1 < n:
        return None
    return sum(xs[i - n + 1:i + 1]) / n


def ema_series(xs, n):
    out, k, e = [], 2.0 / (n + 1), None
    for i, x in enumerate(xs):
        if i + 1 < n:
            out.append(None)
            continue
        e = sum(xs[:n]) / n if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def true_ranges(bars):
    tr = [bars[0]["h"] - bars[0]["l"]]
    for p, b in zip(bars, bars[1:]):
        tr.append(max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])))
    return tr


def atr_series(bars, n=14):
    tr = true_ranges(bars)
    out = [None] * len(tr)
    if len(tr) < n:
        return out
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def weekly(bars):
    weeks = []
    for b in bars:
        y, w, _ = dt.date.fromisoformat(b["date"]).isocalendar()
        if weeks and weeks[-1]["key"] == (y, w):
            k = weeks[-1]
            k["h"], k["l"], k["c"] = max(k["h"], b["h"]), min(k["l"], b["l"]), b["c"]
        else:
            weeks.append({"key": (y, w), "h": b["h"], "l": b["l"], "c": b["c"]})
    return weeks


# ----------------------------------------------------------------- prices

def tick_for(currency):
    return 0.05 if currency == "INR" else 0.01


def up(x, t):
    return round(math.ceil(round(x / t, 6)) * t, 2)


def down(x, t):
    return round(math.floor(round(x / t, 6)) * t, 2)


def money(x, cur):
    return "%s%s" % (SYMBOLS.get(cur, cur + " "), format(round(x, 2), ",.2f"))


# ----------------------------------------------------------------- the scan

def analyse(sym, meta, bars):
    cur = meta.get("currency") or ("INR" if sym.endswith((".NS", ".BO")) else "USD")
    t = tick_for(cur)
    out = {"symbol": sym, "name": meta.get("longName") or meta.get("shortName") or sym,
           "currency": cur, "exchange": meta.get("exchangeName")}
    if len(bars) < 60:
        out.update(status="NO DATA", note="only %d daily candles, need 60" % len(bars))
        return out
    C = [b["c"] for b in bars]
    H = [b["h"] for b in bars]
    L = [b["l"] for b in bars]
    V = [b["v"] for b in bars]
    i = len(bars) - 1
    c = C[i]
    e20 = ema_series(C, 20)
    ema20 = e20[i]
    s50, s200 = sma(C, 50), sma(C, 200)
    s200_ago = sma(C, 200, i - 20) if i >= 219 else None
    rsi = rsi_series(C)
    atrs = atr_series(bars)
    atr = atrs[i]
    tr = true_ranges(bars)
    vol50 = sma(V, 50, i - 1) if i >= 50 else None
    hi55 = max(H[max(0, i - 55):i])
    hi52 = max(H[max(0, i - 252):i])
    w = weekly(bars)
    wc = [k["c"] for k in w]
    w10, w30 = sma(wc, 10), sma(wc, 30)

    if s200 and s200_ago and c > s50 > s200 and s200 > s200_ago:
        trend = "uptrend"
    elif s200 and c < s50 < s200:
        trend = "downtrend"
    else:
        trend = "sideways"
    weekly_up = bool(w30 and w10 and wc[-1] > w30 and w10 > w30)

    out.update({
        "as_of": bars[i]["date"], "close": round(c, 2), "trend": trend, "weekly_up": weekly_up,
        "ema20": round(ema20, 2), "sma50": round(s50, 2),
        "sma200": round(s200, 2) if s200 else None,
        "rsi14": round(rsi[i], 1) if rsi[i] is not None else None,
        "atr14": round(atr, 2), "high_55d": round(hi55, 2), "high_52w": round(hi52, 2),
        "vol_vs_50d": round(V[i] / vol50, 2) if vol50 else None,
    })

    def make(name, entry, stop, triggered=False):
        entry = up(entry, t)
        # keep the stop between 0.5 and 3 ATR under the entry
        stop = min(stop, entry - 0.5 * atr)
        stop = max(stop, entry - 3 * atr)
        stop = down(stop, t)
        risk = entry - stop
        if risk <= 0:
            return None
        s = {"setup": name, "label": LABELS[name], "entry": entry, "stop": stop,
             "risk": round(risk, 2), "risk_pct": round(risk / entry * 100, 2),
             "target_2r": up(entry + 2 * risk, t), "target_3r": up(entry + 3 * risk, t),
             "triggered": triggered,
             "distance_pct": round((entry - c) / c * 100, 2)}
        if hi52 > entry:
            s["resistance_52w"] = round(hi52, 2)
            s["rr_to_52w_high"] = round((hi52 - entry) / risk, 2)
        return s

    setups = []
    up_ok = trend == "uptrend" or (weekly_up and s200 and c > s200)

    # breakout: today closed above the 55-day high on 1.5x volume
    if trend != "downtrend" and c > hi55 and vol50 and V[i] >= 1.5 * vol50:
        s = make("breakout", c, max(min(L[i - 9:i + 1]), hi55 - atr), triggered=True)
        if s:
            s["over_pivot_pct"] = round((c - hi55) / hi55 * 100, 2)
            setups.append(s)

    # bull flag: 15-bar pole up 15 %+, then 6 tight bars that keep the gains
    pole_h, pole_l = H[i - 20:i - 5], L[i - 20:i - 5]
    flag_h, flag_l = H[i - 5:i + 1], L[i - 5:i + 1]
    lo, hi = min(pole_l), max(pole_h)
    height = hi - lo
    if (up_ok and lo > 0 and height / lo >= 0.15 and pole_l.index(lo) < pole_h.index(hi)
            and max(flag_h) <= hi * 1.01 and min(flag_l) >= hi - 0.5 * height
            and max(flag_h) - min(flag_l) <= 0.5 * height):
        s = make("bull_flag", max(flag_h) + t, min(flag_l) - t)
        if s:
            setups.append(s)

    # base: within 5 % under the 55-day high and the ranges have tightened
    tr10, tr50 = sum(tr[i - 9:i + 1]) / 10, sum(tr[i - 49:i + 1]) / 50
    if trend == "uptrend" and hi55 * 0.95 <= c <= hi55 and tr10 < 0.85 * tr50:
        s = make("base", hi55 + t, min(L[i - 9:i + 1]) - t)
        if s:
            setups.append(s)

    # pullbacks: was stretched above the average in the last 15 bars, now back at it
    stretched = any(e20[j] is not None and H[j] >= e20[j] + 2 * atrs[j] for j in range(i - 15, i))
    low3 = min(L[i - 2:i + 1])
    near = H[i] - c <= atr     # a buy-stop above today's high only makes sense near the close
    if up_ok and stretched and near and rsi[i] and 35 <= rsi[i] <= 60:
        stop = min(L[i - 4:i + 1]) - 0.25 * atr
        if low3 <= ema20 + 0.25 * atr and c >= ema20 - 0.5 * atr:
            s = make("pullback_ema20", H[i] + t, stop)
            if s:
                setups.append(s)
        elif low3 <= s50 + 0.25 * atr and c >= s50 - 0.5 * atr:
            s = make("pullback_sma50", H[i] + t, stop)
            if s:
                setups.append(s)

    # oversold dip above a rising long-term trend
    if near and s200 and c > s200 and weekly_up and rsi[i] is not None and rsi[i] <= 35:
        s = make("oversold_dip", H[i] + t, min(L[i - 4:i + 1]) - 0.25 * atr)
        if s:
            setups.append(s)

    setups.sort(key=lambda s: PRIORITY.index(s["setup"]))
    out["setups"] = setups
    if setups and setups[0]["triggered"]:
        s = setups[0]
        out["status"] = "EXTENDED" if s.get("over_pivot_pct", 0) > 5 else "TRIGGERED"
    elif c > ema20 + 3 * atr:
        out["status"] = "EXTENDED"
        out["note"] = "%.1f ATR above its 20-day EMA: wait for a pullback" % ((c - ema20) / atr)
    elif setups:
        out["status"] = "SETUP"
    elif trend == "downtrend":
        out["status"] = "DOWNTREND"
    else:
        out["status"] = "NO SETUP"
    return out


ORDER = {"TRIGGERED": 0, "SETUP": 1, "EXTENDED": 2, "NO SETUP": 3, "DOWNTREND": 4, "NO DATA": 5, "ERROR": 6}


def cmd_scan(a):
    syms = split_tickers([a.symbols]) if a.symbols else load("watchlist.json", {}).get("symbols", [])
    if not syms:
        sys.exit("swing.py: the watchlist is empty. Add tickers: swing.py add RELIANCE.NS TCS.NS AAPL")
    now_ts = time.time() if not a.now else dt.datetime.fromisoformat(a.now).replace(tzinfo=dt.timezone.utc).timestamp()
    results, live = [], []
    for n, sym in enumerate(syms, 1):
        if not a.json and len(syms) > 15 and n % 10 == 0:
            print("  %d of %d" % (n, len(syms)), file=sys.stderr)
        try:
            meta, bars = fetch(sym, a.offline)
        except (RuntimeError, OSError, ValueError) as e:
            results.append({"symbol": sym, "status": "ERROR", "note": str(e)})
            continue
        if not a.include_today:
            bars, dropped = drop_live_bar(meta, bars, now_ts)
            if dropped:
                live.append(sym)
        results.append(analyse(sym, meta, bars))
    results.sort(key=lambda r: (ORDER.get(r["status"], 9), r["setups"][0]["distance_pct"] if r.get("setups") else 0))
    scan = {"scanned_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "count": len(syms), "live_session_dropped": live, "results": results}
    if not a.no_write:
        save("scan.json", scan)
    if a.json:
        print(json.dumps(scan, indent=2))
        return 0 if any(r.get("setups") for r in results) else 1
    print_scan(scan)
    if not a.no_write:
        print("Wrote %s." % path("scan.json"))
    return 0 if any(r.get("setups") for r in results) else 1


def print_scan(scan):
    res = scan["results"]
    ok = [r for r in res if r["status"] not in ("ERROR", "NO DATA")]
    print("Swing scan of %d tickers, daily candles from Yahoo (free, can lag). %d read, %d failed."
          % (scan["count"], len(ok), len(res) - len(ok)))
    if scan["live_session_dropped"]:
        print("Market open for %s: today's unfinished candle is left out, so this is as of the last close."
              % ", ".join(scan["live_session_dropped"]))
    for r in res:
        cur = r.get("currency", "USD")
        if r["status"] in ("ERROR", "NO DATA"):
            print("\n%-12s %s: %s" % (r["symbol"], r["status"], r.get("note", "")))
            continue
        trend = r["trend"] + (", weekly up" if r["weekly_up"] else ", weekly not up")
        print("\n%-12s %-9s %s  close %s (%s)  %s  RSI %s"
              % (r["symbol"], r["status"], r["name"][:28], money(r["close"], cur), r["as_of"], trend, r["rsi14"]))
        if r.get("note"):
            print("    %s" % r["note"])
        if not r.get("setups"):
            c = r["close"]
            print("    levels: 55-day high %s (%+.1f%%), 20-day EMA %s (%+.1f%%), 50-day %s (%+.1f%%)" % (
                money(r["high_55d"], cur), (r["high_55d"] - c) / c * 100,
                money(r["ema20"], cur), (r["ema20"] - c) / c * 100,
                money(r["sma50"], cur), (r["sma50"] - c) / c * 100))
        for s in r.get("setups", []):
            how = "bought at the close" if s["triggered"] else "buy-stop above %s (%+.1f%% from the close)" % (
                money(s["entry"], cur), s["distance_pct"])
            print("    %s" % s["label"])
            print("      entry %s: %s" % (money(s["entry"], cur), how))
            line = "      stop %s (risk %s a share, %.1f%%)  targets 2R %s, 3R %s" % (
                money(s["stop"], cur), money(s["risk"], cur), s["risk_pct"],
                money(s["target_2r"], cur), money(s["target_3r"], cur))
            if "rr_to_52w_high" in s:
                line += "  52-week high %s = %.1fR" % (money(s["resistance_52w"], cur), s["rr_to_52w_high"])
            print(line)
    print("\nA setup is a chart that matched a rule, not a buy. Check results/earnings dates and news first.")
    print("Next: swing.py plan TICKER to size one, inside your risk per trade.")


# ----------------------------------------------------------------- watchlist

def cmd_add(a):
    wl = load("watchlist.json", {"symbols": []})
    new = [s for s in split_tickers(a.tickers, a.file) if s not in wl["symbols"]]
    wl["symbols"] += new
    save("watchlist.json", wl)
    print("Added %d: %s. The watchlist has %d tickers." % (len(new), ", ".join(new) or "nothing new", len(wl["symbols"])))


def cmd_remove(a):
    wl = load("watchlist.json", {"symbols": []})
    gone = set(split_tickers(a.tickers, a.file))
    wl["symbols"] = [s for s in wl["symbols"] if s not in gone]
    save("watchlist.json", wl)
    print("Watchlist: %s" % (", ".join(wl["symbols"]) or "(empty)"))


def cmd_list(a):
    wl = load("watchlist.json", {"symbols": []})
    print("Watchlist (%d): %s" % (len(wl["symbols"]), ", ".join(wl["symbols"]) or "(empty)"))


def cmd_config(a):
    cfg = load("config.json", {})
    for acc in a.account or []:
        cur, _, amt = acc.partition("=")
        try:
            cfg.setdefault("accounts", dict(DEFAULT_CONFIG["accounts"]))[cur.strip().upper()] = float(amt.replace(",", ""))
        except ValueError:
            sys.exit("swing.py: --account takes CUR=AMOUNT, like INR=500000")
    for k in ("risk_pct", "max_position_pct", "trigger_days", "time_stop_days"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    if any([a.account, a.risk_pct, a.max_position_pct, a.trigger_days, a.time_stop_days]):
        save("config.json", cfg)
    print(json.dumps(dict(DEFAULT_CONFIG, **cfg), indent=2))


# ----------------------------------------------------------------- plan

def cmd_plan(a):
    sym = norm(a.ticker)
    cfg = config()
    scan = load("scan.json", {"results": []})
    r = next((x for x in scan["results"] if x["symbol"] == sym), None)
    if r is None or a.fresh:
        meta, bars = fetch(sym, a.offline)
        bars, _ = drop_live_bar(meta, bars, time.time())
        r = analyse(sym, meta, bars)
    cur = r.get("currency", "USD")
    t = tick_for(cur)
    s = None
    if a.entry and a.stop:
        entry, stop = up(a.entry, t), down(a.stop, t)
        if stop >= entry:
            sys.exit("swing.py: the stop %s must be under the entry %s (long only)." % (stop, entry))
        risk = round(entry - stop, 2)
        s = {"setup": "manual", "label": "Your own entry and stop", "entry": entry, "stop": stop,
             "risk": risk, "risk_pct": round(risk / entry * 100, 2),
             "target_2r": up(entry + 2 * risk, t), "target_3r": up(entry + 3 * risk, t), "triggered": False}
    else:
        options = r.get("setups") or []
        if a.setup:
            options = [x for x in options if x["setup"] == a.setup]
        if not options:
            sys.exit("swing.py: %s has no %s setup in the last scan (status %s). Give --entry and --stop to plan your own."
                     % (sym, a.setup or "swing", r.get("status")))
        s = options[0]
    if a.target:
        s["target_2r"] = up(a.target, t)
    acct = a.account or (cfg.get("accounts") or {}).get(cur)
    if not acct:
        sys.exit("swing.py: no account size for %s. Run: swing.py config --account %s=AMOUNT" % (cur, cur))
    risk_amt = acct * cfg["risk_pct"] / 100
    by_risk = int(risk_amt // s["risk"])
    by_cap = int(acct * cfg["max_position_pct"] / 100 // s["entry"])
    qty = min(by_risk, by_cap)
    bound = "your %.2g%% risk per trade (%s)" % (cfg["risk_pct"], money(risk_amt, cur)) if by_risk <= by_cap \
        else "your %.0f%% position cap (%s)" % (cfg["max_position_pct"], money(acct * cfg["max_position_pct"] / 100, cur))
    if qty < 1:
        sys.exit("swing.py: no trade. One share risks %s, more than %s. Skip it or use a closer stop."
                 % (money(s["risk"], cur), bound))
    plan = {
        "id": "%s-%s" % (sym, dt.date.today().isoformat()), "symbol": sym, "currency": cur,
        "created": dt.date.today().isoformat(), "as_of": r.get("as_of"), "setup": s["setup"],
        "entry": s["entry"], "stop": s["stop"], "target_2r": s["target_2r"], "target_3r": s["target_3r"],
        "qty": qty, "cost": round(qty * s["entry"], 2), "max_loss": round(qty * s["risk"], 2),
        "account": acct, "bound_by": bound, "triggered": s["triggered"],
        "trigger_days": cfg["trigger_days"], "time_stop_days": cfg["time_stop_days"],
        "status": "open" if s["triggered"] else "pending",
    }
    m = lambda x: money(x, cur)
    print("SWING PLAN  %s  (%s)  - not an order, nothing is placed" % (sym, s["label"]))
    if s["triggered"]:
        print("  Entry   about %s: it already broke out at the close. Buy near that price, skip it if it gaps far above." % m(s["entry"]))
    else:
        print("  Entry   buy-stop at %s (stop-limit, limit about 0.5%% higher), good for %d sessions."
              % (m(s["entry"]), cfg["trigger_days"]))
        print("          Cancel it if not hit in %d sessions, or if it opens more than 1 ATR above the entry." % cfg["trigger_days"])
    print("  Stop    %s, risk %s a share (%.1f%%)" % (m(s["stop"]), m(s["risk"]), s["risk_pct"]))
    print("  Size    %d shares = %s, bound by %s" % (qty, m(plan["cost"]), bound))
    print("  Max loss about %s if the stop fills at %s (a gap can fill it worse)" % (m(plan["max_loss"]), m(s["stop"])))
    print("  Targets 2R %s (sell half), 3R %s or trail the rest" % (m(s["target_2r"]), m(s["target_3r"])))
    print("  Manage  at +1R (%s) move the stop to the entry. After 2R trail the rest under the 20-day EMA (on a close)."
          % m(s["entry"] + s["risk"]))
    print("          Time stop: out if it has not reached +1R in %d sessions. Do not hold through results/earnings."
          % cfg["time_stop_days"])
    if a.write:
        plans = load("plans.json", {"plans": []})
        plans["plans"] = [p for p in plans["plans"] if p["id"] != plan["id"]] + [plan]
        save("plans.json", plans)
        print("Wrote %s. Check it each evening: swing.py review" % path("plans.json"))
    return 0


# ----------------------------------------------------------------- review

def walk(p, bars):
    """Replay a plan over the daily candles after it was made. Daily bars cannot
    show which came first inside one day, so a day that touches both the stop
    and a target counts as the stop (the cautious reading)."""
    status, stop = p["status"], p["stop"]
    events, waited, held, reached_1r = [], 0, 0, False
    one_r = p["entry"] + (p["entry"] - p["stop"])
    for b in bars:
        if status == "pending":
            waited += 1
            if b["h"] >= p["entry"]:
                fill = max(b["o"], p["entry"])
                status = "open"
                events.append("%s triggered, filled about %.2f" % (b["date"], fill))
            elif waited >= p["trigger_days"]:
                events.append("%s not triggered in %d sessions: cancel the order" % (b["date"], waited))
                return "expired", stop, events
            else:
                continue
        if status in ("open", "half_out"):
            held += 1
            if b["l"] <= stop:
                events.append("%s stop hit at %.2f" % (b["date"], stop))
                return ("stopped" if status == "open" else "closed"), stop, events
            if b["h"] >= one_r and not reached_1r:
                reached_1r = True
                stop = max(stop, p["entry"])
                events.append("%s reached +1R: stop moves to the entry %.2f" % (b["date"], stop))
            if status == "open" and b["h"] >= p["target_2r"]:
                status = "half_out"
                events.append("%s target 2R %.2f: sell half, trail the rest" % (b["date"], p["target_2r"]))
            if b["h"] >= p["target_3r"]:
                events.append("%s target 3R %.2f" % (b["date"], p["target_3r"]))
                return "closed", stop, events
            if held >= p["time_stop_days"] and not reached_1r:
                events.append("%s time stop: %d sessions without +1R, exit" % (b["date"], held))
                return "time_stop", stop, events
    return status, stop, events


def cmd_review(a):
    plans = load("plans.json", {"plans": []})
    live = [p for p in plans["plans"] if p["status"] in ("pending", "open", "half_out")]
    if not live:
        print("No pending or open plans in %s." % path("plans.json"))
        return 1
    for p in live:
        try:
            meta, bars = fetch(p["symbol"], a.offline)
        except (RuntimeError, OSError, ValueError) as e:
            print("%s: could not read prices (%s)" % (p["symbol"], e))
            continue
        bars, _ = drop_live_bar(meta, bars, time.time())
        after = [b for b in bars if b["date"] > (p.get("as_of") or p["created"])]
        orig = {"status": p["status"], "stop": p["stop"]}
        # replay from the plan as written, so a review run twice gives the same answer
        p0 = dict(p, status="open" if p["triggered"] else "pending", stop=p.get("initial_stop", p["stop"]))
        status, stop, events = walk(p0, after)
        p.setdefault("initial_stop", p0["stop"])
        p["status"], p["stop"] = status, stop
        last = bars[-1]
        print("%s  %s  last close %s (%s)" % (p["symbol"], status.upper().replace("_", " "),
                                             money(last["c"], p["currency"]), last["date"]))
        idle = ("nothing yet: waiting for %s" % money(p["entry"], p["currency"]) if status == "pending"
                else "holding: stop %s, next target %s" % (money(stop, p["currency"]), money(
                    p["target_3r"] if status == "half_out" else p["target_2r"], p["currency"])))
        for e in events or [idle]:
            print("    " + e)
        if orig != {"status": p["status"], "stop": p["stop"]}:
            print("    (updated: was %s, stop %s)" % (orig["status"], orig["stop"]))
    if not a.no_write:
        save("plans.json", plans)
    print("Daily candles only: check your broker for real fills.")
    return 0


# ----------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("add", cmd_add), ("remove", cmd_remove)):
        p = sub.add_parser(name)
        p.add_argument("tickers", nargs="*")
        p.add_argument("--file", help="a file of tickers, e.g. a TradingView watchlist export")
        p.set_defaults(fn=fn)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("config")
    p.add_argument("--account", action="append", help="CUR=AMOUNT, e.g. INR=500000; repeat for USD")
    p.add_argument("--risk-pct", type=float)
    p.add_argument("--max-position-pct", type=float)
    p.add_argument("--trigger-days", type=int)
    p.add_argument("--time-stop-days", type=int)
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("scan")
    p.add_argument("--symbols", help="comma list instead of the watchlist")
    p.add_argument("--offline", help="folder of saved Yahoo chart JSON, SYMBOL.json")
    p.add_argument("--include-today", action="store_true", help="keep today's unfinished candle")
    p.add_argument("--now", help="pin the clock (UTC, ISO format), for tests")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("plan")
    p.add_argument("ticker")
    p.add_argument("--setup", choices=PRIORITY)
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float, help="replace the 2R target")
    p.add_argument("--account", type=float, help="account size for this plan")
    p.add_argument("--fresh", action="store_true", help="fetch prices again instead of the last scan")
    p.add_argument("--offline")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("review")
    p.add_argument("--offline")
    p.add_argument("--no-write", action="store_true")
    p.set_defaults(fn=cmd_review)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())

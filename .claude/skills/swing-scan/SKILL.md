---
name: swing-scan
description: >-
  Swing Scanner for a personal watchlist of US and Indian (NSE/BSE) stocks.
  Keeps the watchlist (add, remove, list), reads free Yahoo daily candles,
  builds weekly candles, and checks every ticker for a long swing setup:
  breakout above the 55-day high on heavy volume, a tight base under it, a
  bull flag, a pullback to the 20-day EMA or 50-day average in an uptrend, or
  an oversold dip above the 200-day average. Gives each setup an entry
  (buy-stop), a stop, 2R and 3R targets and the room to the 52-week high, in
  rupees or dollars. Use when the user says "scan my watchlist", "any swing
  setups", "what's setting up", "add RELIANCE to my watchlist", "remove TSLA",
  "show my watchlist", "is INFY in an uptrend", "swing entries", or at the
  end of a trading day.
argument-hint: "[add|remove|list|scan] [tickers]"
---

# swing-scan

A swing trade is held for days to a few weeks, not minutes. This scanner
checks the user's own watchlist once a day, on completed daily candles, and
lists the charts that match a rule. A setup is a chart that matched a rule,
not a buy, not a prediction and not advice. Most people who trade actively
lose money; this tool does not change that.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run everything from the
repository root, so the files land in `swing/`.

```bash
python3 "${CLAUDE_SKILL_DIR}/swing.py" add RELIANCE.NS TCS.NS AAPL     # NSE .NS, BSE .BO, US no suffix
python3 "${CLAUDE_SKILL_DIR}/swing.py" add nse:infy bse:sbin           # NSE:/BSE: prefixes work too
python3 "${CLAUDE_SKILL_DIR}/swing.py" remove TCS.NS
python3 "${CLAUDE_SKILL_DIR}/swing.py" list
python3 "${CLAUDE_SKILL_DIR}/swing.py" scan                            # the whole watchlist -> swing/scan.json
python3 "${CLAUDE_SKILL_DIR}/swing.py" scan --symbols HDFCBANK.NS,NVDA # a one-off list, watchlist untouched
python3 "${CLAUDE_SKILL_DIR}/swing.py" scan --json                     # machine-readable
```

Exit code 0 when at least one setup was found, 1 when none (or the
watchlist is empty).

## Files (in `swing/`, committed to the repo)

| file | what |
| --- | --- |
| `swing/watchlist.json` | `{"symbols": [...]}` |
| `swing/config.json` | account size per currency, risk per trade (see swing-plan) |
| `swing/scan.json` | the last scan: per ticker status, trend, levels, `setups[]` |
| `swing/plans.json` | plans written by swing-plan |

**Cloud sessions are wiped when they end.** After `add`, `remove`, a scan the
user wants to keep, or any plan change, commit `swing/` and push it to the
current branch (`git add swing && git commit -m "Update watchlist" && git push`),
so the next session starts with the same watchlist and plans. Say that you did.

## Tickers

- NSE: `RELIANCE.NS`, `TCS.NS`, `HDFCBANK.NS`. BSE: the NSE-style symbol plus
  `.BO` (`RELIANCE.BO`), not the numeric scrip code.
- US: plain tickers, `AAPL`, `BRK-B` (Yahoo uses a dash for class shares).
- Renamed or demerged stocks change ticker (Tata Motors' passenger business
  is `TMPV.NS` since the 2025 demerger). An `ERROR` line says Yahoo has no
  data: check the ticker, do not guess a replacement without telling the user.

## What it computes (daily candles, about 2 years)

- 20-day EMA, 50- and 200-day simple averages, RSI 14 (Wilder), ATR 14,
  the 55-day high (before today), the 52-week high, volume against its
  50-day average, weekly 10 and 30-week averages.
- **Trend**: uptrend when close > 50-day > 200-day and the 200-day is higher
  than 20 sessions ago; downtrend when close < 50-day < 200-day; else sideways.
  **Weekly up** when the weekly close is over the 30-week average and the
  10-week is over the 30-week.
- While a market is open, today's unfinished candle is left out (the scan
  says so), so the scan is as of the last close. NSE and US hours differ:
  the best time to scan both is after the US close (after 2:30 AM IST, or
  any time before 9:15 AM IST for India and 9:30 AM ET for the US).

## The setups (long only)

| setup | rule | entry | stop |
| --- | --- | --- | --- |
| `breakout` | close above the 55-day high, volume at least 1.5x the 50-day average, not a downtrend | the close (already triggered) | 10-day low or 1 ATR under the old high, whichever is higher |
| `bull_flag` | a 15-bar run of 15 %+, then 6 bars that stay within the top half of the run, range at most half of it | above the flag high | under the flag low |
| `base` | uptrend, close within 5 % under the 55-day high, the last 10 bars' range under 85 % of the 50-bar average | above the 55-day high | under the 10-day low |
| `pullback_ema20` | daily uptrend, or weekly up and above the 200-day, stretched 2 ATR above the EMA in the last 15 bars, now back to it, RSI 35 to 60 | above today's high | under the 5-day low less 0.25 ATR |
| `pullback_sma50` | the same, back to the 50-day average | above today's high | the same |
| `oversold_dip` | above the 200-day, weekly up, RSI 14 at or under 35 | above today's high | under the 5-day low less 0.25 ATR |

The pullbacks and the oversold dip need today's high within 1 ATR of the close: after a
big down candle a buy-stop above its high would be far from the price, so no setup is given.

Every stop is kept between 0.5 and 3 ATR under the entry. Prices round to the
tick: Rs 0.05 for INR, $0.01 for USD (entries up, stops down). Targets are
2R and 3R (R = entry minus stop); the room to the 52-week high is shown in R
when the high is above the entry.

Status per ticker: `TRIGGERED` (a breakout at the close), `SETUP` (a buy-stop
level to wait for), `EXTENDED` (more than 3 ATR over the 20-day EMA, or a
breakout more than 5 % past the old high: do not chase), `NO SETUP` (shows the
55-day high, 20-day EMA and 50-day levels), `DOWNTREND`, `NO DATA`, `ERROR`.

## Steps

1. If the watchlist is empty, ask the user for tickers and `add` them (then commit and push).
2. `scan`. Tell the user how many tickers were read and failed, the date of
   the candles, then the TRIGGERED and SETUP names with entry, stop, risk
   and targets in the stock's own currency. Keep NO SETUP names to one line.
3. Remind them to check the results / earnings date and the news before any
   entry: a swing trade held through results can gap past the stop.
4. The user picks one. Hand it to `/swing-plan TICKER`. Never pick for them.

## Rules

- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a profit figure or a return. R multiples and levels only.
- Data is free Yahoo end-of-day data: unofficial, can lag or be wrong.
  Check prices with the broker before acting.
- A setup is stale after a day. Re-scan before acting on it.

## Safety

- Education and tooling, not financial advice.
- It never places an order and never asks for broker logins or keys.
- Long only. No options, futures, margin, intraday leverage or short selling.

---
name: swing-plan
description: >-
  Swing Planner. Turns one setup from /swing-scan (or the user's own entry and
  stop) into a written trade plan: buy-stop entry valid for 3 sessions, stop,
  position size from the account and the risk per trade (1 % by default, in
  rupees for Indian stocks and dollars for US ones), capped at 25 % of the
  account, 2R and 3R targets, move-to-breakeven at +1R, a trailing rule and a
  15-session time stop. Then reviews saved plans each evening on daily candles:
  triggered, stopped, target hit, expired or time stop. Never places an order.
  Use when the user says "plan this trade", "how many shares of RELIANCE",
  "size it", "I have 5 lakh", "risk 1 %", "set my account size", "review my
  trades", "did my stop hit", "check my swing positions", or after /swing-scan.
argument-hint: "[TICKER] [--entry X --stop Y]"
---

# swing-plan

`${CLAUDE_SKILL_DIR}/../swing-scan/swing.py` is the tool (shared with
swing-scan). Run it from the repository root.

```bash
SW="${CLAUDE_SKILL_DIR}/../swing-scan/swing.py"
python3 "$SW" config                                    # show the settings
python3 "$SW" config --account INR=500000 --account USD=5000
python3 "$SW" config --risk-pct 1 --max-position-pct 25 --trigger-days 3 --time-stop-days 15
python3 "$SW" plan ICICIBANK.NS                          # the top setup from the last scan
python3 "$SW" plan AAPL --setup pullback_ema20           # a particular setup
python3 "$SW" plan TCS.NS --entry 2480 --stop 2390       # the user's own levels
python3 "$SW" plan MSFT --write                          # save it to swing/plans.json
python3 "$SW" review                                     # replay saved plans on the newest candles
```

`config --account CUR=AMOUNT` can be repeated (INR, USD). Defaults:
INR 5,00,000, USD 5,000, 1 % risk per trade, 25 % position cap, 3 sessions
for a buy-stop to trigger, a 15-session time stop. Ask the user for their own
account sizes and risk before the first plan, and save them with `config`.

## How it sizes

shares = the smaller of
- risk per trade / (entry - stop), and
- position cap / entry,

rounded down to whole shares. It says which limit decided it. If one share
risks more than the risk per trade, it refuses: no trade, not a smaller one.

## The plan it prints

- **Entry**: a buy-stop (stop-limit, limit about 0.5 % over) at the entry,
  valid `trigger_days` sessions. Cancel if it is not hit, or if the stock
  opens more than 1 ATR above the entry (a gap you would chase). A breakout
  that already triggered is "buy near the close, skip it if it gaps far up".
- **Stop**, risk a share, size, cost, **max loss at the stop** (a gap can fill worse).
- **Targets**: sell half at 2R, the rest at 3R or trailed.
- **Management**: at +1R the stop moves to the entry; after 2R trail the rest
  under the 20-day EMA on a closing basis; out after `time_stop_days` sessions
  without +1R; do not hold through results / earnings.

In India, a buy-stop is a "SL" order (trigger price plus limit) on most
brokers (Zerodha, Upstox, Groww, Angel); GTT orders can hold one for days.
In the US it is a stop-limit buy, good for the day or GTC. The user places
it themselves.

## Review

`review` replays each pending or open plan over the daily candles since it
was made, from the plan as written, so running it twice gives the same
answer. Daily candles cannot show what came first inside one day, so a day
that touches both the stop and a target counts as the stop. Statuses:
pending, open, half_out, stopped, closed, expired, time_stop. Tell the user
what changed and what to do (cancel the order, move the stop, sell half).
Fills at their broker are the truth: if they tell you a real fill, plan
again with `--entry` at that price.

After `plan --write` or `review`, commit `swing/` and push it, so the plans
survive the session.

## Rules

- One plan per stock, shown before anything is saved. Never "plan all".
- Never place, send or confirm an order. Never ask for broker logins or keys.
- Never print a profit figure or a return. Max loss and R multiples only.
- Education and tooling, not financial advice. Most active traders lose money.
- Long only, cash only: no margin, MTF, F&O, options or short selling.

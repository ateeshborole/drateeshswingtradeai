# drateeshswingtradeai

Swing-trade skills for Claude Code on the web, for a watchlist of **US and
Indian (NSE/BSE) stocks**. Start a session from this repo and the skills load
from `.claude/skills/`:

| command | what it does |
| --- | --- |
| `/swing-scan` | Keeps your watchlist and checks every stock on daily candles for a long swing setup: breakout, tight base, bull flag, pullback to the 20-day EMA or 50-day average, oversold dip. Gives entry, stop, 2R / 3R targets in Rs or $. |
| `/swing-plan` | Sizes one setup to your account and risk per trade (1 % by default), writes the plan (entry, stop, targets, breakeven, trailing and time stop) and reviews saved plans each evening. |

Ask in plain words too: "add RELIANCE.NS and AAPL to my watchlist", "scan my
watchlist", "plan ICICIBANK", "review my trades".

Tickers: NSE `RELIANCE.NS`, BSE `RELIANCE.BO`, US `AAPL`.

Your watchlist, settings and plans are saved in `swing/` and committed to
this repo, because cloud sessions are wiped when they end.

## The tool

Standard-library Python 3.8+, free Yahoo Finance daily data, nothing to install.

```bash
python3 .claude/skills/swing-scan/swing.py add RELIANCE.NS TCS.NS AAPL
python3 .claude/skills/swing-scan/swing.py scan
python3 .claude/skills/swing-scan/swing.py config --account INR=500000 --account USD=5000 --risk-pct 1
python3 .claude/skills/swing-scan/swing.py plan RELIANCE.NS --write
python3 .claude/skills/swing-scan/swing.py review
python3 -m unittest discover -s tests -v      # offline tests
```

The cloud environment needs `query1.finance.yahoo.com` and
`query2.finance.yahoo.com` in its allowed hosts.

## Fine print

Not financial advice. A setup is a chart that matched a rule, not a
prediction. Most people who trade actively lose money. The data is free and
unofficial and can lag. Nothing here places an order: you place every order
yourself at your broker. Long only, no margin, F&O or short selling.
A stop is a plan, not a guarantee: a gap can fill it far worse.

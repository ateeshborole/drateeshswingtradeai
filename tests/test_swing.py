"""Offline tests for swing.py: synthetic candles, no network.

Run: python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "swing", os.path.join(HERE, "..", ".claude", "skills", "swing-scan", "swing.py"))
swing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(swing)

DAY = 86400
START = 1735689600  # 2025-01-01 00:00 UTC


def bars_from(closes, vols=None, spread=0.01):
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        out.append({"ts": START + i * DAY, "date": "d%04d" % i, "o": prev,
                    "h": max(prev, c) * (1 + spread), "l": min(prev, c) * (1 - spread),
                    "c": c, "v": (vols or [1000000] * len(closes))[i]})
    return out


def dated(bars):
    import datetime as dt
    for i, b in enumerate(bars):
        b["date"] = (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
    return bars


def yahoo_json(bars, currency="USD"):
    return {"chart": {"error": None, "result": [{
        "meta": {"currency": currency, "longName": "Test Co", "gmtoffset": 0,
                 "currentTradingPeriod": {"regular": {"start": 0, "end": 1}}},
        "timestamp": [b["ts"] for b in bars],
        "indicators": {"quote": [{k: [b[x] for b in bars] for k, x in
                                  (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}]}}]}}


class Indicators(unittest.TestCase):
    def test_rsi_all_gains_is_100(self):
        self.assertEqual(swing.rsi_series(list(range(1, 30)))[-1], 100.0)

    def test_sma_and_ema(self):
        xs = [float(x) for x in range(1, 41)]
        self.assertEqual(swing.sma(xs, 10), 35.5)
        self.assertAlmostEqual(swing.ema_series([5.0] * 30, 20)[-1], 5.0)

    def test_ticks_and_names(self):
        self.assertEqual(swing.up(100.01, 0.05), 100.05)
        self.assertEqual(swing.down(99.99, 0.05), 99.95)
        self.assertEqual(swing.norm("nse:reliance"), "RELIANCE.NS")
        self.assertEqual(swing.norm("BSE:Tcs"), "TCS.BO")
        self.assertEqual(swing.norm(" aapl "), "AAPL")


class Setups(unittest.TestCase):
    def test_breakout_on_volume_is_triggered(self):
        closes = [100 * 1.002 ** i for i in range(259)]
        closes.append(closes[-1] * 1.04)
        vols = [1000000] * 259 + [3000000]
        r = swing.analyse("TEST", {"currency": "USD"}, dated(bars_from(closes, vols)))
        self.assertEqual(r["trend"], "uptrend")
        self.assertEqual(r["status"], "TRIGGERED")
        s = r["setups"][0]
        self.assertEqual(s["setup"], "breakout")
        self.assertLess(s["stop"], s["entry"])
        self.assertAlmostEqual(s["target_2r"] - s["entry"], 2 * s["risk"], delta=0.02)

    def test_downtrend_has_no_long_setup(self):
        closes = [200 * 0.997 ** i for i in range(260)]
        r = swing.analyse("DOWN", {"currency": "INR"}, dated(bars_from(closes)))
        self.assertEqual(r["status"], "DOWNTREND")
        self.assertEqual(r["setups"], [])

    def test_short_history(self):
        r = swing.analyse("NEW", {"currency": "USD"}, dated(bars_from([10.0] * 30)))
        self.assertEqual(r["status"], "NO DATA")

    def test_inr_prices_round_to_five_paise(self):
        closes = [1000 * 1.002 ** i for i in range(259)]
        closes.append(closes[-1] * 1.04)
        r = swing.analyse("X.NS", {"currency": "INR"}, dated(bars_from(closes, [1] * 259 + [5])))
        s = r["setups"][0]
        for k in ("entry", "stop", "target_2r"):
            self.assertAlmostEqual(round(s[k] / 0.05) * 0.05, s[k], places=6)


class Review(unittest.TestCase):
    PLAN = {"entry": 100.0, "stop": 95.0, "target_2r": 110.0, "target_3r": 115.0,
            "status": "pending", "trigger_days": 3, "time_stop_days": 15}

    def bar(self, d, o, h, l, c):
        return {"date": d, "o": o, "h": h, "l": l, "c": c}

    def test_expires_when_never_triggered(self):
        bars = [self.bar("d%d" % i, 98, 99, 97, 98) for i in range(3)]
        self.assertEqual(swing.walk(self.PLAN, bars)[0], "expired")

    def test_trigger_then_breakeven_then_stopped_at_entry(self):
        bars = [self.bar("a", 99, 101, 98, 100.5),   # triggers
                self.bar("b", 101, 106, 100.5, 105),  # +1R: stop to 100
                self.bar("c", 104, 104, 99.5, 100)]   # stopped at the entry
        status, stop, ev = swing.walk(self.PLAN, bars)
        self.assertEqual((status, stop), ("stopped", 100.0))

    def test_same_day_stop_and_target_counts_as_stop(self):
        bars = [self.bar("a", 99, 101, 98, 100.5), self.bar("b", 100, 112, 94, 100)]
        self.assertEqual(swing.walk(self.PLAN, bars)[0], "stopped")

    def test_half_out_then_closed_at_3r(self):
        bars = [self.bar("a", 99, 101, 98, 100.5), self.bar("b", 101, 111, 100.5, 110),
                self.bar("c", 110, 116, 109, 115)]
        self.assertEqual(swing.walk(self.PLAN, bars)[0], "closed")


class EndToEnd(unittest.TestCase):
    def test_scan_and_plan_offline(self):
        closes = [100 * 1.002 ** i for i in range(259)]
        closes.append(closes[-1] * 1.04)
        bars = bars_from(closes, [1000000] * 259 + [3000000])
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "data")
            os.makedirs(data)
            with open(os.path.join(data, "TEST.json"), "w") as f:
                json.dump(yahoo_json(bars), f)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                import contextlib, io
                with contextlib.redirect_stdout(io.StringIO()):
                    swing.main(["add", "test"])
                    self.assertEqual(swing.main(["scan", "--offline", data]), 0)
                    swing.main(["config", "--account", "USD=10000"])
                    swing.main(["plan", "TEST", "--write"])
                with open("swing/plans.json") as f:
                    plans = json.load(f)["plans"]
                p = plans[0]
                self.assertEqual(p["status"], "open")
                self.assertLessEqual(p["max_loss"], 100.0)          # 1 % of 10,000
                self.assertLessEqual(p["cost"], 2500.0)            # 25 % cap
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()

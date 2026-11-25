from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml_engine.live_trend_breakout import closed_candles, latest_signal, trade_still_open


def _df(closes, start="2025-01-01 00:00:00", spread=1.0):
    n = len(closes)
    t = pd.date_range(start, periods=n, freq="1h")
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": c,
                         "close": c, "high": c + spread, "low": c - spread})


def test_forming_candle_is_dropped():
    df = _df([1, 2, 3, 4], start="2025-01-01 00:00:00")
    # last candle opens 03:00 (1H), now is 03:00:05 -> still forming
    now = datetime(2025, 1, 1, 3, 0, 5, tzinfo=timezone.utc)
    assert len(closed_candles(df, now)) == 3
    now = datetime(2025, 1, 1, 4, 0, 5, tzinfo=timezone.utc)
    assert len(closed_candles(df, now)) == 4


def test_breakout_up_and_flat():
    up = _df(list(np.linspace(100, 160, 200)) + [200])
    assert latest_signal(up, 55, 100) == "BUY"
    flat = _df([100.0] * 220)
    assert latest_signal(flat, 55, 100) == "NEUTRAL"
    assert latest_signal(_df([1, 2, 3]), 55, 100) == "NEUTRAL"


def test_trade_open_then_stopped_and_break_even():
    base = _df([100.0] * 10)
    t0 = base["datetime"].iloc[2]
    # nothing hit after entry -> open
    assert trade_still_open("BUY", 100, 96, 112, t0, base, 1.0)
    # stop hit
    hit = base.copy(); hit.loc[5, "low"] = 95
    assert not trade_still_open("BUY", 100, 96, 112, t0, hit, 1.0)
    # 1R reached (high 104) then back to entry -> stopped at break-even
    be = base.copy(); be.loc[4, "high"] = 104.5; be.loc[6, "low"] = 99.9
    assert not trade_still_open("BUY", 100, 96, 112, t0, be, 1.0)
    # sell target hit
    s = base.copy(); s.loc[5, "low"] = 87
    assert not trade_still_open("SELL", 100, 104, 88, t0, s, 1.0)

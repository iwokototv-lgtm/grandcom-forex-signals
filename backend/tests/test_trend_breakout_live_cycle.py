import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import gold_server_v3 as gs


class FakeColl:
    def __init__(self): self.docs = []
    async def find_one(self, q, sort=None):
        m = [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]
        return m[-1] if m else None
    async def insert_one(self, d): self.docs.append(d)


class FakeDB:
    def __init__(self): self.gold_signals = FakeColl()


def _frame(closed_minutes_ago=2, n=1500):
    n = n
    last_open = datetime.now(timezone.utc) - timedelta(hours=1, minutes=closed_minutes_ago)
    t = [last_open - timedelta(hours=1 * (n - 1 - i)) for i in range(n)]
    c = np.concatenate([np.linspace(2000, 2300, n - 1), [2400.0]])
    df = pd.DataFrame({"datetime": [x.strftime("%Y-%m-%d %H:%M:%S") for x in t],
                       "open": c, "close": c, "high": c + 3, "low": c - 3})
    # forming candle on the end
    forming = df.iloc[-1:].copy()
    forming["datetime"] = (last_open + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    return pd.concat([df, forming], ignore_index=True)


def _run(monkeypatch, frame, strategies=("trend_breakout",)):
    db = FakeDB(); sent = []
    async def fetch(*a, **k): return frame.copy()
    async def send(*a, **k): sent.append((a, k)); return True
    async def no_blackout(pair): return False
    async def risk_ok(): return {"trading_allowed": True}
    monkeypatch.setattr(gs, "fetch_ohlcv_with_retry", fetch)
    monkeypatch.setattr(gs, "send_to_telegram", send)
    monkeypatch.setattr(gs, "get_db", lambda: db)
    monkeypatch.setattr(gs._calendar_filter, "is_blackout_period", no_blackout)
    monkeypatch.setattr(gs._risk_manager, "enforce_risk_limits", risk_ok)
    monkeypatch.setattr(gs, "TB_ACTIVE", list(strategies))
    for name in strategies:
        asyncio.run(gs.generate_trend_breakout_signal("XAUUSD", name))
    return db, sent


def test_signal_sent_once_and_levels(monkeypatch):
    db, sent = _run(monkeypatch, _frame())
    assert len(sent) == 1 and len(db.gold_signals.docs) == 1
    d = db.gold_signals.docs[0]
    assert d["type"] == "BUY" and d["strategy"] == "trend_breakout"
    risk = d["entry_price"] - d["sl_price"]
    assert d["tp_levels"][0] == pytest.approx(d["entry_price"] + risk, rel=1e-3)
    assert d["tp_levels"][2] == pytest.approx(d["entry_price"] + 3 * risk, rel=1e-3)


def test_same_candle_not_repeated(monkeypatch):
    frame = _frame()
    db, sent = _run(monkeypatch, frame)
    async def fetch(*a, **k): return frame.copy()
    monkeypatch.setattr(gs, "fetch_ohlcv_with_retry", fetch)
    asyncio.run(gs.generate_trend_breakout_signal("XAUUSD"))
    assert len(db.gold_signals.docs) == 1


def test_stale_candle_skipped(monkeypatch):
    db, sent = _run(monkeypatch, _frame(closed_minutes_ago=120))
    assert sent == [] and db.gold_signals.docs == []


def test_future_timestamps_not_signalled(monkeypatch):
    # feed labels 12h ahead of UTC (what the live feed returned without timezone=UTC)
    frame = _frame(closed_minutes_ago=-600)
    db, sent = _run(monkeypatch, frame)
    assert sent == [] and db.gold_signals.docs == []


def test_slow_variant_signals_with_its_own_label(monkeypatch):
    db, sent = _run(monkeypatch, _frame(n=1500), strategies=("trend_breakout_slow",))
    assert len(sent) == 1
    assert db.gold_signals.docs[0]["strategy"] == "trend_breakout_slow"
    assert sent[0][1]["strategy_label"] == "Trend breakout 480 (1H)"


def test_two_variants_never_open_two_trades_on_one_pair(monkeypatch):
    db, sent = _run(monkeypatch, _frame(n=1500), strategies=("trend_breakout", "trend_breakout_slow"))
    assert len(sent) == 1 and len(db.gold_signals.docs) == 1
    assert db.gold_signals.docs[0]["strategy"] == "trend_breakout"


def test_live_strategy_list_parsing():
    assert gs.TB_VARIANTS["trend_breakout_slow"] == (480, 1200)
    assert gs.TB_ACTIVE == ["trend_breakout", "trend_breakout_slow"] or gs.TB_ACTIVE == []

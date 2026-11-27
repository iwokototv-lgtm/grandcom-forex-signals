import asyncio
import os

import pineconnector_output as p


def _env(monkeypatch, **kw):
    for k in ("PINECONNECTOR_ENABLED", "PINECONNECTOR_WEBHOOK_URL", "PINECONNECTOR_KEY", "PINECONNECTOR_VOL_LOTS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_disabled_by_default(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_KEY="k", PINECONNECTOR_WEBHOOK_URL="http://x")
    assert p.is_enabled() is False
    assert asyncio.run(p.send_signal("XAUUSD", "BUY")) is False


def test_build_alert_xauusd_only(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_KEY="60123456789")
    assert p.build_alert("XAUUSD", "BUY", "0.01") == "60123456789,buy,XAUUSD,vol_lots=0.01"
    assert p.build_alert("XAUUSD", "SELL", "0.01") == "60123456789,sell,XAUUSD,vol_lots=0.01"
    assert p.build_alert("XAUEUR", "BUY", "0.01") is None
    assert p.build_alert("XAUUSD", "NEUTRAL", "0.01") is None


def test_enabled_requires_url_and_key(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_KEY="k")
    assert p.is_enabled() is False
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_WEBHOOK_URL="http://x")
    assert p.is_enabled() is False


def test_network_error_returns_false(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_KEY="k",
         PINECONNECTOR_WEBHOOK_URL="http://127.0.0.1:9/x")
    assert asyncio.run(p.send_signal("XAUUSD", "BUY")) is False

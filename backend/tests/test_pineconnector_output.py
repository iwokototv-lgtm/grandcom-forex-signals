import asyncio
import os

import pineconnector_output as p


def _env(monkeypatch, **kw):
    for k in ("PINECONNECTOR_ENABLED", "PINECONNECTOR_WEBHOOK_URL", "PINECONNECTOR_KEY",
              "PINECONNECTOR_KEY_XAUUSD", "PINECONNECTOR_KEY_XAUEUR", "PINECONNECTOR_VOL_LOTS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_disabled_by_default(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_KEY_XAUUSD="k", PINECONNECTOR_WEBHOOK_URL="http://x")
    assert p.is_enabled() is False
    assert asyncio.run(p.send_signal("XAUUSD", "BUY")) is False


def test_build_alert_uses_key_per_pair(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_KEY_XAUUSD="111", PINECONNECTOR_KEY_XAUEUR="222")
    assert p.build_alert("XAUUSD", "BUY", "0.01") == "111,buy,XAUUSD,vol_lots=0.01"
    assert p.build_alert("XAUUSD", "SELL", "0.01") == "111,sell,XAUUSD,vol_lots=0.01"
    assert p.build_alert("XAUEUR", "BUY", "0.01") == "222,buy,XAUEUR,vol_lots=0.01"
    assert p.build_alert("EURUSD", "BUY", "0.01") is None
    assert p.build_alert("XAUUSD", "NEUTRAL", "0.01") is None


def test_pair_without_key_is_not_sent(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_KEY_XAUUSD="111")
    assert p.build_alert("XAUEUR", "BUY", "0.01") is None


def test_enabled_requires_url(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_KEY_XAUUSD="k")
    assert p.is_enabled() is False
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_WEBHOOK_URL="http://x")
    assert p.is_enabled() is True


def test_network_error_returns_false(monkeypatch):
    _env(monkeypatch, PINECONNECTOR_ENABLED="true", PINECONNECTOR_KEY_XAUUSD="k",
         PINECONNECTOR_WEBHOOK_URL="http://127.0.0.1:9/x")
    assert asyncio.run(p.send_signal("XAUUSD", "BUY")) is False

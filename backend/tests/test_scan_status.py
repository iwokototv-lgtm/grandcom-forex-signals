import scan_status


def test_record_keeps_latest_per_pair_and_strategy():
    scan_status._latest.clear()
    scan_status.record("XAUUSD", "legacy", "NEUTRAL", confidence=20.0)
    scan_status.record("XAUUSD", "legacy", "BUY", confidence=62.5)
    rows = scan_status.snapshot()
    assert len(rows) == 1
    assert rows[0]["signal"] == "BUY"
    assert rows[0]["confidence"] == 62.5


def test_render_escapes_and_shows_next_runs():
    scan_status._latest.clear()
    scan_status.record("XAUUSD", "<b>x</b>", "sell")
    page = scan_status.render_html(
        scan_status.snapshot(),
        [("run_signal_generation", "2026-11-26 19:00:05+00:00")],
        "legacy hybrid",
    )
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<b>SELL</b>" in page
    assert "run_signal_generation: 2026-11-26 19:00:05+00:00" in page
    assert 'content="60"' in page


def test_render_empty_state():
    scan_status._latest.clear()
    page = scan_status.render_html([], [], "legacy hybrid")
    assert "No scan has run" in page

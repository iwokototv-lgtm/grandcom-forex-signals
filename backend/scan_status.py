"""Latest scan result per pair, for the status page served at the engine's root.

Only in-memory state: it is rebuilt by the next scheduled scan after a restart.
Nothing here sends to Telegram or changes a signal decision.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

_latest: dict[str, dict] = {}


def record(pair: str, strategy: str, signal: str, confidence: float | None = None, candle: str | None = None) -> None:
    """Remember the most recent scan result for a pair and strategy."""
    _latest[f"{pair}|{strategy}"] = {
        "pair": pair,
        "strategy": strategy,
        "signal": str(signal).upper(),
        "confidence": confidence,
        "candle": candle,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def snapshot() -> list[dict]:
    """All recorded results, newest first."""
    return sorted(_latest.values(), key=lambda r: r["scanned_at"], reverse=True)


def render_html(rows: list[dict], next_runs: list[tuple[str, str]], mode: str) -> str:
    """Plain HTML page. Shows the last result per pair and the next scheduled runs."""
    body_rows = "".join(
        "<tr>"
        f"<td>{html.escape(r['pair'])}</td>"
        f"<td>{html.escape(r['strategy'])}</td>"
        f"<td><b>{html.escape(r['signal'])}</b></td>"
        f"<td>{'' if r['confidence'] is None else html.escape(str(r['confidence']))}</td>"
        f"<td>{html.escape(str(r['candle'] or ''))}</td>"
        f"<td>{html.escape(r['scanned_at'])}</td>"
        "</tr>"
        for r in rows
    ) or '<tr><td colspan="6">No scan has run since the last restart.</td></tr>'
    next_items = "".join(
        f"<li>{html.escape(name)}: {html.escape(when)}</li>" for name, when in next_runs
    ) or "<li>No scheduled jobs.</li>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Signal scans</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #111; }}
table {{ border-collapse: collapse; width: 100%; max-width: 960px; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #f5f5f5; }}
</style></head>
<body>
<h2>Signal scans</h2>
<p>Mode: <b>{html.escape(mode)}</b>. Page refreshes every 60 seconds. Results are from this process since its last restart.</p>
<table>
<tr><th>Pair</th><th>Strategy</th><th>Last result</th><th>Confidence</th><th>Candle</th><th>Scanned at (UTC)</th></tr>
{body_rows}
</table>
<h3>Next scheduled runs (UTC)</h3>
<ul>{next_items}</ul>
</body></html>"""

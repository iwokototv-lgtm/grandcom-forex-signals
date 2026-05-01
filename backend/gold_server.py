"""
Grandcom Gold Signals Server — A+ ELITE EDITION
Standalone backend for XAUUSD & XAUEUR signals
Sends to @grandcomgold Telegram channel
Designed for Railway deployment

A+ Elite Edition Features:
  - 20 Modular Gates (toggleable)
  - Market DNA System (self-learning ATR/spread/volatility)
  - Kill Switch / RiskCommander (global drawdown protection)
  - Execution Feedback Loop (slippage & TP1 net profit tracking)
  - Blackbox Logging (JSONL audit trail + CSV denial log)
  - Kelly Criterion Position Sizing (quarter-Kelly, capped 2%)
  - Breakeven & Trailing Stops (async monitors)
  - Correlation Matrix Monitor (XAUUSD vs DXY auto-gate)
  - Daily Intelligence Report (07:00 UTC)
  - Telegram Admin Commands (/gate /dna /kill /status /report)
  - Session Confidence Filter (per-session min scores)
  - Advanced Indicators: Hurst, Shannon Entropy, Volume Profile,
    Keltner Channels, Liquidity Sweep, Gold-Silver Ratio
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
import os
import csv
import json
import math
import re
import asyncio
import logging
import aiohttp
import ta
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient
import litellm

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_server")

# ============================================================
# CONFIG
# ============================================================
MONGO_URL                 = os.environ.get("MONGO_URL")
DB_NAME                   = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL_ID  = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")
TELEGRAM_ADMIN_ID         = os.environ.get("TELEGRAM_ADMIN_ID")
TWELVE_DATA_API_KEY       = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY            = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")
MT5_API_URL               = os.environ.get("MT5_API_URL")          # optional EA bridge

BLACKBOX_JSONL  = "blackbox_audit.jsonl"
DENIAL_CSV      = "signal_denials.csv"

# Gold pair configuration — ATR-based swing strategy
GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.5,
        "atr_multiplier_tp3": 2.2,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.5,
        "atr_multiplier_tp3": 2.2,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
}

# ============================================================
# SESSION CONFIDENCE FILTER
# ============================================================
SESSION_CONFIDENCE = {
    "LONDON_NY_OVERLAP": {"hours": (12, 16), "min_score": 60},
    "LONDON":            {"hours": (7,  12), "min_score": 65},
    "NEW_YORK":          {"hours": (16, 21), "min_score": 65},
    "ASIAN":             {"hours": (0,   7), "min_score": 78},
    "DEAD_ZONE":         {"hours": (21, 24), "min_score": 85},
}

def get_current_session() -> tuple[str, int]:
    """Return (session_name, min_score) for the current UTC hour."""
    hour = datetime.now(timezone.utc).hour
    for name, cfg in SESSION_CONFIDENCE.items():
        h_start, h_end = cfg["hours"]
        if h_start <= hour < h_end:
            return name, cfg["min_score"]
    return "DEAD_ZONE", 85

# ============================================================
# 20 MODULAR GATES — toggleable flags
# ============================================================
GATES: dict[str, bool] = {
    "gate_01_news_guard":          True,
    "gate_02_h4_mtf":              True,
    "gate_03_dxy_correlation":     True,
    "gate_04_candlestick_pa":      True,
    "gate_05_choppy_market":       True,
    "gate_06_circuit_breaker":     True,
    "gate_07_session_confidence":  True,
    "gate_08_shannon_entropy":     True,
    "gate_09_hurst_exponent":      True,
    "gate_10_keltner_channel":     True,
    "gate_11_obv_divergence":      False,   # DISABLED — no real volume
    "gate_12_liquidity_sweep":     True,
    "gate_13_order_block":         True,
    "gate_14_gold_silver_ratio":   True,
    "gate_15_vw_macd":             False,   # DISABLED — no real volume
    "gate_16_daily_drawdown":      True,
    "gate_17_signal_throttle":     True,
    "gate_18_volume_profile_poc":  True,
    "gate_19_kelly_criterion":     True,
    "gate_20_correlation_matrix":  True,
}

# ============================================================
# DB
# ============================================================
client = AsyncIOMotorClient(MONGO_URL)
db     = client[DB_NAME]

# ============================================================
# KILL SWITCH STATE
# ============================================================
_kill_switch_active = False
_kill_switch_reason = ""

def is_kill_switch_active() -> bool:
    return _kill_switch_active

async def activate_kill_switch(reason: str):
    global _kill_switch_active, _kill_switch_reason
    _kill_switch_active = True
    _kill_switch_reason = reason
    logger.critical(f"🚨 KILL SWITCH ACTIVATED: {reason}")
    await db.kill_switch_log.insert_one({
        "active": True,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc),
    })
    await _alert_admin(f"🚨 KILL SWITCH ACTIVATED\nReason: {reason}")

async def deactivate_kill_switch():
    global _kill_switch_active, _kill_switch_reason
    _kill_switch_active = False
    _kill_switch_reason = ""
    logger.info("✅ Kill switch deactivated")
    await db.kill_switch_log.insert_one({
        "active": False,
        "reason": "manual_reset",
        "timestamp": datetime.now(timezone.utc),
    })

# ============================================================
# DAILY DRAWDOWN TRACKER
# ============================================================
_daily_losses: list[dict] = []   # {"pair": str, "pips": float, "ts": datetime}

def record_daily_loss(pair: str, pips: float):
    _daily_losses.append({"pair": pair, "pips": pips, "ts": datetime.now(timezone.utc)})

def get_today_losses() -> list[dict]:
    today = datetime.now(timezone.utc).date()
    return [x for x in _daily_losses if x["ts"].date() == today]

# ============================================================
# LAST SIGNAL TIMESTAMPS (Gate 17 throttle)
# ============================================================
_last_signal_ts: dict[str, datetime] = {}

# ============================================================
# BREAKEVEN TRACKING
# ============================================================
_breakeven_count  = 0
_trailing_count   = 0

# ============================================================
# MARKET DNA SYSTEM
# ============================================================
class MarketDNA:
    """
    Self-learning market profile per pair.
    Persists to MongoDB for multi-session continuity.
    Updates via EMA: 90% old + 10% new.
    """

    VOLATILITY_CLASSES = ["LOW", "MEDIUM", "HIGH", "EXTREME"]

    def __init__(self, pair: str):
        self.pair              = pair
        self.avg_spread        = 0.30          # pips
        self.atr_14            = 10.0          # price units
        self.volatility_class  = "MEDIUM"
        self.sl_clamp_mult     = 1.5
        self.spread_guard_mult = 1.0
        self.avg_slippage      = 0.0           # pips
        self.tp1_buffer_extra  = 0.0           # pips
        self._loaded           = False

    async def load(self):
        doc = await db.market_dna.find_one({"pair": self.pair})
        if doc:
            self.avg_spread        = doc.get("avg_spread",        self.avg_spread)
            self.atr_14            = doc.get("atr_14",            self.atr_14)
            self.volatility_class  = doc.get("volatility_class",  self.volatility_class)
            self.sl_clamp_mult     = doc.get("sl_clamp_mult",     self.sl_clamp_mult)
            self.spread_guard_mult = doc.get("spread_guard_mult", self.spread_guard_mult)
            self.avg_slippage      = doc.get("avg_slippage",      self.avg_slippage)
            self.tp1_buffer_extra  = doc.get("tp1_buffer_extra",  self.tp1_buffer_extra)
        self._loaded = True

    async def save(self):
        await db.market_dna.update_one(
            {"pair": self.pair},
            {"$set": {
                "pair":              self.pair,
                "avg_spread":        self.avg_spread,
                "atr_14":            self.atr_14,
                "volatility_class":  self.volatility_class,
                "sl_clamp_mult":     self.sl_clamp_mult,
                "spread_guard_mult": self.spread_guard_mult,
                "avg_slippage":      self.avg_slippage,
                "tp1_buffer_extra":  self.tp1_buffer_extra,
                "updated_at":        datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    def update_atr(self, new_atr: float):
        """EMA update; auto-shift volatility class if ATR spikes >30%."""
        old_atr = self.atr_14
        self.atr_14 = 0.90 * old_atr + 0.10 * new_atr

        if old_atr > 0 and self.atr_14 > old_atr * 1.30:
            idx = self.VOLATILITY_CLASSES.index(self.volatility_class)
            if idx < len(self.VOLATILITY_CLASSES) - 1:
                self.volatility_class = self.VOLATILITY_CLASSES[idx + 1]
                logger.info(f"DNA {self.pair}: volatility upgraded → {self.volatility_class}")

        self._recalc_multipliers()

    def update_spread(self, new_spread: float):
        self.avg_spread = 0.90 * self.avg_spread + 0.10 * new_spread

    def _recalc_multipliers(self):
        mapping = {
            "LOW":     (1.3, 0.8),
            "MEDIUM":  (1.5, 1.0),
            "HIGH":    (1.8, 1.3),
            "EXTREME": (2.2, 1.8),
        }
        self.sl_clamp_mult, self.spread_guard_mult = mapping.get(
            self.volatility_class, (1.5, 1.0)
        )

    def learn_from_execution(self, slippage_pips: float, tp1_net_profit: float):
        """Adjust guards based on live execution feedback."""
        self.avg_slippage = 0.90 * self.avg_slippage + 0.10 * slippage_pips
        if self.avg_slippage > 0.3:
            self.spread_guard_mult = min(self.spread_guard_mult + 0.05, 3.0)
        if tp1_net_profit < 0:
            self.tp1_buffer_extra = min(self.tp1_buffer_extra + 0.1, 2.0)

    def snapshot(self) -> dict:
        return {
            "pair":              self.pair,
            "avg_spread":        round(self.avg_spread, 4),
            "atr_14":            round(self.atr_14, 4),
            "volatility_class":  self.volatility_class,
            "sl_clamp_mult":     round(self.sl_clamp_mult, 2),
            "spread_guard_mult": round(self.spread_guard_mult, 2),
            "avg_slippage":      round(self.avg_slippage, 4),
            "tp1_buffer_extra":  round(self.tp1_buffer_extra, 4),
        }


# Global DNA instances
_dna: dict[str, MarketDNA] = {pair: MarketDNA(pair) for pair in GOLD_PAIRS}


async def adjust_execution_quality(pair: str, slippage_pips: float, tp1_net_profit: float):
    """Gate 19 feedback: update DNA from live execution data."""
    dna = _dna.get(pair)
    if dna:
        dna.learn_from_execution(slippage_pips, tp1_net_profit)
        await dna.save()
        logger.info(
            f"DNA {pair} updated — slippage={slippage_pips:.3f}p "
            f"tp1_net={tp1_net_profit:.2f} "
            f"spread_guard={dna.spread_guard_mult:.2f}"
        )


# ============================================================
# RISK COMMANDER (Kill Switch)
# ============================================================
class RiskCommander:
    """
    Monitors global account drawdown.
    Triggers kill switch at 5% account equity drawdown.
    """

    def __init__(self, max_dd_pct: float = 5.0, mock_mode: bool = True):
        self.max_dd_pct   = max_dd_pct
        self.mock_mode    = mock_mode
        self.peak_equity  = 10_000.0   # mock starting equity
        self.cur_equity   = 10_000.0

    async def fetch_equity(self) -> float | None:
        if self.mock_mode or not MT5_API_URL:
            return self.cur_equity
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{MT5_API_URL}/equity", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    return float(data.get("equity", 0))
        except Exception as e:
            logger.warning(f"MT5 equity fetch failed: {e}")
            return None

    async def check(self):
        if is_kill_switch_active():
            return
        equity = await self.fetch_equity()
        if equity is None:
            return
        self.cur_equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd_pct = (self.peak_equity - equity) / self.peak_equity * 100
        if dd_pct >= self.max_dd_pct:
            await activate_kill_switch(
                f"Global drawdown {dd_pct:.2f}% ≥ {self.max_dd_pct}% threshold"
            )


_risk_commander = RiskCommander(mock_mode=(MT5_API_URL is None))


# ============================================================
# BLACKBOX LOGGING
# ============================================================
def _blackbox_log(event: dict):
    """Append a JSONL entry to the institutional audit trail."""
    try:
        with open(BLACKBOX_JSONL, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as e:
        logger.warning(f"Blackbox log error: {e}")


def _denial_log(pair: str, gate: str, reason: str, price: float, adx: float, rsi: float, score: float):
    """Append a row to the CSV denial log."""
    try:
        write_header = not os.path.exists(DENIAL_CSV)
        with open(DENIAL_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "pair", "gate", "reason", "price", "adx", "rsi", "score"])
            w.writerow([
                datetime.now(timezone.utc).isoformat(),
                pair, gate, reason,
                round(price, 2), round(adx, 2), round(rsi, 2), round(score, 2),
            ])
    except Exception as e:
        logger.warning(f"Denial log error: {e}")


def _gate_blocked(pair: str, gate: str, reason: str, indicators: dict, score: float):
    """Unified helper: log to blackbox + denial CSV."""
    _blackbox_log({
        "event":      "SIGNAL_BLOCKED",
        "pair":       pair,
        "gate":       gate,
        "reason":     reason,
        "score":      score,
        "indicators": {k: indicators.get(k) for k in
                       ["current_price", "adx", "rsi", "atr", "williams_r", "cci"]},
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })
    _denial_log(
        pair, gate, reason,
        indicators.get("current_price", 0),
        indicators.get("adx", 0),
        indicators.get("rsi", 50),
        score,
    )


# ============================================================
# KELLY CRITERION POSITION SIZING
# ============================================================
async def calculate_kelly_fraction(pair: str) -> dict:
    """
    Fetch last 50 closed trades for the pair, compute quarter-Kelly fraction.
    Returns: {"kelly_pct": float, "win_rate": float, "avg_win": float, "avg_loss": float}
    """
    try:
        trades = await db.gold_signals.find(
            {"pair": pair, "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2", "CLOSED_TP3", "CLOSED_SL"]}},
            {"pips": 1, "status": 1},
        ).sort("created_at", -1).limit(50).to_list(50)

        if len(trades) < 5:
            return {"kelly_pct": 1.0, "win_rate": 0.5, "avg_win": 10.0, "avg_loss": 10.0}

        wins  = [t["pips"] for t in trades if t.get("status", "").startswith("CLOSED_TP") and t.get("pips", 0) > 0]
        losses= [abs(t["pips"]) for t in trades if t.get("status") == "CLOSED_SL" and t.get("pips", 0) < 0]

        if not wins or not losses:
            return {"kelly_pct": 1.0, "win_rate": 0.5, "avg_win": 10.0, "avg_loss": 10.0}

        win_rate = len(wins) / len(trades)
        avg_win  = sum(wins)   / len(wins)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0:
            return {"kelly_pct": 2.0, "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss}

        kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        quarter_kelly = max(0.0, kelly / 4)
        capped = min(quarter_kelly * 100, 2.0)   # cap at 2% risk

        return {
            "kelly_pct": round(capped, 3),
            "win_rate":  round(win_rate, 3),
            "avg_win":   round(avg_win, 2),
            "avg_loss":  round(avg_loss, 2),
        }
    except Exception as e:
        logger.error(f"Kelly criterion error: {e}")
        return {"kelly_pct": 1.0, "win_rate": 0.5, "avg_win": 10.0, "avg_loss": 10.0}


# ============================================================
# BREAKEVEN & TRAILING STOPS
# ============================================================
async def check_breakeven():
    """
    When TP1 is hit on an ACTIVE signal, move SL to entry (risk-free).
    Runs every 5 minutes.
    """
    global _breakeven_count
    try:
        signals = await db.gold_signals.find(
            {"status": "ACTIVE", "breakeven_set": {"$ne": True}}
        ).to_list(100)

        for sig in signals:
            pair  = sig.get("pair")
            stype = sig.get("type", "").upper()
            entry = sig.get("entry_price", 0)
            tps   = sig.get("tp_levels", [])
            if not tps:
                continue

            price = await _get_live_price(pair)
            if price is None:
                continue

            tp1_hit = (stype == "BUY" and price >= tps[0]) or \
                      (stype == "SELL" and price <= tps[0])

            if tp1_hit:
                await db.gold_signals.update_one(
                    {"_id": sig["_id"]},
                    {"$set": {"sl_price": entry, "breakeven_set": True}},
                )
                _breakeven_count += 1
                logger.info(f"✅ Breakeven set for {pair} signal — SL moved to entry {entry}")
                await _alert_admin(f"✅ Breakeven set: {pair} SL → {entry}")
    except Exception as e:
        logger.error(f"Breakeven check error: {e}")


async def update_trailing_stops():
    """
    After breakeven is set, trail SL by 2.5×ATR below current price (BUY)
    or above current price (SELL).
    Runs every 5 minutes.
    """
    global _trailing_count
    try:
        signals = await db.gold_signals.find(
            {"status": "ACTIVE", "breakeven_set": True}
        ).to_list(100)

        for sig in signals:
            pair  = sig.get("pair")
            stype = sig.get("type", "").upper()
            dna   = _dna.get(pair)
            atr   = dna.atr_14 if dna else 10.0
            trail = 2.5 * atr

            price = await _get_live_price(pair)
            if price is None:
                continue

            if stype == "BUY":
                new_sl = round(price - trail, 2)
                if new_sl > sig.get("sl_price", 0):
                    await db.gold_signals.update_one(
                        {"_id": sig["_id"]},
                        {"$set": {"sl_price": new_sl}},
                    )
                    _trailing_count += 1
                    logger.info(f"📈 Trailing SL updated: {pair} BUY → {new_sl}")
            elif stype == "SELL":
                new_sl = round(price + trail, 2)
                if new_sl < sig.get("sl_price", float("inf")):
                    await db.gold_signals.update_one(
                        {"_id": sig["_id"]},
                        {"$set": {"sl_price": new_sl}},
                    )
                    _trailing_count += 1
                    logger.info(f"📉 Trailing SL updated: {pair} SELL → {new_sl}")
    except Exception as e:
        logger.error(f"Trailing stop update error: {e}")


# ============================================================
# CORRELATION MATRIX MONITOR (Gate 20)
# ============================================================
async def monitor_correlation_matrix():
    """
    Fetch XAUUSD vs DXY 50-candle returns.
    Normal correlation: -0.7 to -0.9.
    If correlation > -0.3 → auto-disable Gate 03.
    If restored → auto-enable Gate 03.
    """
    try:
        xau_df = await get_price_data("XAUUSD", interval="1h", outputsize=52)
        dxy_df = await get_generic_price_data("DXY",   interval="1h", outputsize=52)

        if xau_df is None or dxy_df is None or len(xau_df) < 50 or len(dxy_df) < 50:
            logger.warning("Correlation monitor: insufficient data")
            return

        xau_ret = xau_df["close"].pct_change().dropna().tail(50)
        dxy_ret = dxy_df["close"].pct_change().dropna().tail(50)

        min_len = min(len(xau_ret), len(dxy_ret))
        corr = float(np.corrcoef(xau_ret.values[-min_len:], dxy_ret.values[-min_len:])[0, 1])

        broken = corr > -0.3
        prev   = GATES.get("gate_03_dxy_correlation", True)

        if broken and prev:
            GATES["gate_03_dxy_correlation"] = False
            msg = f"⚠️ Gate 03 AUTO-DISABLED — DXY correlation broken (corr={corr:.3f})"
            logger.warning(msg)
            await _alert_admin(msg)
        elif not broken and not prev:
            GATES["gate_03_dxy_correlation"] = True
            msg = f"✅ Gate 03 AUTO-ENABLED — DXY correlation restored (corr={corr:.3f})"
            logger.info(msg)
            await _alert_admin(msg)

        await db.correlation_log.insert_one({
            "xauusd_dxy_corr": round(corr, 4),
            "gate_03_active":  GATES["gate_03_dxy_correlation"],
            "timestamp":       datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.error(f"Correlation matrix monitor error: {e}")


# ============================================================
# DAILY INTELLIGENCE REPORT
# ============================================================
async def send_daily_intelligence_report():
    """Runs at 07:00 UTC. Sends full performance + DNA snapshot to admin."""
    try:
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signals_24h = await db.gold_signals.find(
            {"created_at": {"$gte": since}}
        ).to_list(500)

        total   = len(signals_24h)
        wins    = sum(1 for s in signals_24h if s.get("result") == "WIN")
        losses  = sum(1 for s in signals_24h if s.get("result") == "LOSS")
        net_pips= sum(s.get("pips", 0) for s in signals_24h)
        wr      = (wins / total * 100) if total else 0

        dna_lines = []
        kelly_lines = []
        for pair in GOLD_PAIRS:
            dna = _dna.get(pair)
            if dna:
                dna_lines.append(
                    f"  {pair}: ATR={dna.atr_14:.2f} | Vol={dna.volatility_class} | "
                    f"Spread={dna.avg_spread:.3f}"
                )
            k = await calculate_kelly_fraction(pair)
            kelly_lines.append(
                f"  {pair}: {k['kelly_pct']:.2f}% risk | WR={k['win_rate']*100:.1f}%"
            )

        disabled_gates = [g for g, v in GATES.items() if not v]

        report = (
            f"📊 DAILY INTELLIGENCE REPORT — {since.strftime('%Y-%m-%d')}\n\n"
            f"📈 Signals: {total} | Wins: {wins} | Losses: {losses}\n"
            f"🎯 Win Rate: {wr:.1f}% | Net Pips: {net_pips:+.1f}\n\n"
            f"🧬 Market DNA:\n" + "\n".join(dna_lines) + "\n\n"
            f"💰 Kelly Sizing:\n" + "\n".join(kelly_lines) + "\n\n"
            f"🔒 Disabled Gates: {', '.join(disabled_gates) if disabled_gates else 'None'}\n"
            f"✅ Breakeven Moves: {_breakeven_count} | Trailing Updates: {_trailing_count}\n"
            f"🚨 Kill Switch: {'ACTIVE ⚠️' if is_kill_switch_active() else 'Standby ✅'}\n"
            f"\n<i>Grandcom Gold A+ Elite Edition</i>"
        )

        await _alert_admin(report)
        logger.info("📊 Daily intelligence report sent")
    except Exception as e:
        logger.error(f"Daily report error: {e}")


# ============================================================
# TELEGRAM ADMIN HELPERS
# ============================================================
async def _alert_admin(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Admin alert failed: {e}")


# ============================================================
# TELEGRAM ADMIN COMMAND HANDLERS
# ============================================================
async def cmd_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /gate <gate_name_or_number> <on|off>"""
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("Usage: /gate <name_or_number> <on|off>")
            return
        name_raw, state = args[0].lower(), args[1].lower()
        # Allow short form: "01" → "gate_01_news_guard"
        matched = None
        for key in GATES:
            if name_raw in key or key.startswith(f"gate_{name_raw}"):
                matched = key
                break
        if not matched:
            await update.message.reply_text(f"Unknown gate: {name_raw}")
            return
        GATES[matched] = (state == "on")
        await update.message.reply_text(f"✅ {matched} → {'ENABLED' if GATES[matched] else 'DISABLED'}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_dna(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /dna <XAUUSD|XAUEUR>"""
    try:
        pair = (context.args[0].upper() if context.args else "XAUUSD")
        dna  = _dna.get(pair)
        if not dna:
            await update.message.reply_text(f"Unknown pair: {pair}")
            return
        snap = dna.snapshot()
        text = f"🧬 Market DNA — {pair}\n" + "\n".join(f"  {k}: {v}" for k, v in snap.items())
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual kill switch trigger."""
    await activate_kill_switch("Manual trigger via /kill command")
    await update.message.reply_text("🚨 Kill switch ACTIVATED manually.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System status overview."""
    disabled = [g for g, v in GATES.items() if not v]
    text = (
        f"⚙️ SYSTEM STATUS\n"
        f"Kill Switch: {'🚨 ACTIVE' if is_kill_switch_active() else '✅ Standby'}\n"
        f"Disabled Gates: {len(disabled)}\n"
        f"Breakeven Moves: {_breakeven_count}\n"
        f"Trailing Updates: {_trailing_count}\n"
    )
    await update.message.reply_text(text)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger daily report on demand."""
    await send_daily_intelligence_report()
    await update.message.reply_text("📊 Report sent.")


# ============================================================
# PRICE DATA FETCHERS
# ============================================================
async def get_price_data(pair: str, interval: str = "1h", outputsize: int = 100):
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.error(f"No data for {pair}: {data.get('message', 'Unknown error')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_index(ascending=False).reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching {pair}: {e}")
        return None


async def get_generic_price_data(symbol: str, interval: str = "1h", outputsize: int = 100):
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.warning(f"No data for {symbol}: {data.get('message', 'Unknown error')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_index(ascending=False).reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching generic symbol {symbol}: {e}")
        return None


async def _get_live_price(pair: str) -> float | None:
    """Fetch single live price for a pair."""
    try:
        symbol = GOLD_PAIRS.get(pair, {}).get("twelve_data_symbol", pair)
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json()
                return float(data["price"]) if "price" in data else None
    except Exception as e:
        logger.warning(f"Live price fetch error for {pair}: {e}")
        return None


# ============================================================
# ADVANCED INDICATOR FUNCTIONS
# ============================================================
def calculate_hurst_exponent(series: pd.Series, max_lag: int = 20) -> float:
    """
    Estimate Hurst exponent via R/S analysis.
    H < 0.5 → mean-reverting | H ≈ 0.5 → random | H > 0.5 → trending
    """
    try:
        lags   = range(2, max_lag)
        tau    = [np.std(np.subtract(series[lag:].values, series[:-lag].values)) for lag in lags]
        poly   = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        return round(float(poly[0]), 4)
    except Exception:
        return 0.5


def calculate_shannon_entropy(series: pd.Series, bins: int = 10) -> float:
    """
    Shannon entropy of price returns.
    High entropy → chaotic/noisy market.
    """
    try:
        returns = series.pct_change().dropna()
        counts, _ = np.histogram(returns, bins=bins)
        probs = counts / counts.sum()
        probs = probs[probs > 0]
        entropy = -float(np.sum(probs * np.log2(probs)))
        return round(entropy, 4)
    except Exception:
        return 3.0


def calculate_volume_profile(df: pd.DataFrame, bins: int = 20) -> dict:
    """
    Approximate volume profile using price frequency (no real volume).
    Returns POC (Point of Control) and value area bounds.
    """
    try:
        prices = df["close"].dropna()
        counts, edges = np.histogram(prices, bins=bins)
        poc_idx = int(np.argmax(counts))
        poc     = float((edges[poc_idx] + edges[poc_idx + 1]) / 2)
        total   = counts.sum()
        target  = total * 0.70
        acc     = 0
        va_low  = edges[0]
        va_high = edges[-1]
        for i in range(len(counts)):
            acc += counts[i]
            if acc >= target:
                va_high = float(edges[i + 1])
                break
        return {"poc": round(poc, 2), "va_low": round(float(va_low), 2), "va_high": round(va_high, 2)}
    except Exception:
        return {"poc": 0.0, "va_low": 0.0, "va_high": 0.0}


def calculate_keltner_channels(df: pd.DataFrame, window: int = 20, mult: float = 2.0) -> dict:
    """
    Keltner Channels: EMA ± mult×ATR.
    Price outside channels → overextension.
    """
    try:
        ema  = df["close"].ewm(span=window, adjust=False).mean()
        atr  = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=window).average_true_range()
        upper = ema + mult * atr
        lower = ema - mult * atr
        latest = df.iloc[-1]
        return {
            "kc_upper":      round(float(upper.iloc[-1]), 2),
            "kc_lower":      round(float(lower.iloc[-1]), 2),
            "kc_mid":        round(float(ema.iloc[-1]), 2),
            "price_above":   float(latest["close"]) > float(upper.iloc[-1]),
            "price_below":   float(latest["close"]) < float(lower.iloc[-1]),
        }
    except Exception:
        return {"kc_upper": 0.0, "kc_lower": 0.0, "kc_mid": 0.0, "price_above": False, "price_below": False}


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detect stop hunts: price briefly breaks a recent high/low then reverses.
    """
    try:
        if len(df) < lookback + 2:
            return {"sweep_detected": False, "sweep_type": None}
        window   = df.iloc[-(lookback + 2):-2]
        recent_h = float(window["high"].max())
        recent_l = float(window["low"].min())
        last     = df.iloc[-1]
        prev     = df.iloc[-2]
        # Bearish sweep: wick above recent high then close below it
        if float(last["high"]) > recent_h and float(last["close"]) < recent_h:
            return {"sweep_detected": True, "sweep_type": "BEARISH_SWEEP"}
        # Bullish sweep: wick below recent low then close above it
        if float(last["low"]) < recent_l and float(last["close"]) > recent_l:
            return {"sweep_detected": True, "sweep_type": "BULLISH_SWEEP"}
        return {"sweep_detected": False, "sweep_type": None}
    except Exception:
        return {"sweep_detected": False, "sweep_type": None}


async def get_gold_silver_ratio() -> float | None:
    """
    Fetch XAU/XAG ratio. Extreme values (>90 or <60) signal potential reversals.
    """
    try:
        xau_df = await get_generic_price_data("XAU/USD", interval="1h", outputsize=2)
        xag_df = await get_generic_price_data("XAG/USD", interval="1h", outputsize=2)
        if xau_df is None or xag_df is None:
            return None
        xau = float(xau_df["close"].iloc[-1])
        xag = float(xag_df["close"].iloc[-1])
        return round(xau / xag, 2) if xag > 0 else None
    except Exception:
        return None


def get_dynamic_atr_settings(volatility_class: str, hurst: float) -> dict:
    """
    Adjust ATR multipliers based on volatility class and Hurst exponent.
    Trending (H>0.6) → wider TP. Mean-reverting (H<0.4) → tighter TP.
    """
    base = {
        "LOW":     {"sl": 1.3, "tp1": 0.7, "tp2": 1.3, "tp3": 1.9},
        "MEDIUM":  {"sl": 1.5, "tp1": 0.8, "tp2": 1.5, "tp3": 2.2},
        "HIGH":    {"sl": 1.8, "tp1": 1.0, "tp2": 1.8, "tp3": 2.6},
        "EXTREME": {"sl": 2.2, "tp1": 1.2, "tp2": 2.0, "tp3": 3.0},
    }
    mults = base.get(volatility_class, base["MEDIUM"]).copy()
    if hurst > 0.6:
        mults["tp2"] = round(mults["tp2"] * 1.15, 2)
        mults["tp3"] = round(mults["tp3"] * 1.20, 2)
    elif hurst < 0.4:
        mults["tp1"] = round(mults["tp1"] * 0.85, 2)
        mults["tp2"] = round(mults["tp2"] * 0.85, 2)
    return mults


# ============================================================
# CORE INDICATORS
# ============================================================
def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        df["rsi"]        = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_ind         = ta.trend.MACD(close)
        df["macd"]       = macd_ind.macd()
        df["macd_signal"]= macd_ind.macd_signal()
        df["ma_20"]      = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        df["ma_50"]      = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        bb               = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        df["bb_upper"]   = bb.bollinger_hband()
        df["bb_lower"]   = bb.bollinger_lband()
        df["bb_width"]   = (df["bb_upper"] - df["bb_lower"]) / df["ma_20"] * 100
        atr_ind          = ta.volatility.AverageTrueRange(high, low, close, window=14)
        df["atr"]        = atr_ind.average_true_range()
        adx_ind          = ta.trend.ADXIndicator(high, low, close, window=14)
        df["adx"]        = adx_ind.adx()
        stoch_ind        = ta.momentum.StochasticOscillator(high, low, close, window=9, smooth_window=6)
        df["stoch_k"]    = stoch_ind.stoch()
        df["stoch_d"]    = stoch_ind.stoch_signal()
        stochrsi_ind     = ta.momentum.StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
        df["stochrsi_k"] = stochrsi_ind.stochrsi_k()
        df["stochrsi_d"] = stochrsi_ind.stochrsi_d()
        cci_ind          = ta.trend.CCIIndicator(high, low, close, window=14)
        df["cci"]        = cci_ind.cci()
        wr_ind           = ta.momentum.WilliamsRIndicator(high, low, close, lbp=14)
        df["williams_r"] = wr_ind.williams_r()

        latest = df.iloc[-1]
        dp     = params["decimal_places"]
        trend  = "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"

        return {
            "current_price": round(float(latest["close"]), dp),
            "rsi":           float(latest["rsi"]),
            "macd":          float(latest["macd"]),
            "macd_signal":   float(latest["macd_signal"]),
            "ma_20":         round(float(latest["ma_20"]), dp),
            "ma_50":         round(float(latest["ma_50"]), dp),
            "bb_upper":      round(float(latest["bb_upper"]), dp),
            "bb_lower":      round(float(latest["bb_lower"]), dp),
            "bb_width":      round(float(latest["bb_width"]), 4),
            "atr":           round(float(latest["atr"]), dp),
            "trend":         trend,
            "adx":           round(float(latest["adx"]), 2),
            "stoch_k":       round(float(latest["stoch_k"]), 2),
            "stoch_d":       round(float(latest["stoch_d"]), 2),
            "stochrsi_k":    round(float(latest["stochrsi_k"]) * 100, 2),
            "stochrsi_d":    round(float(latest["stochrsi_d"]) * 100, 2),
            "cci":           round(float(latest["cci"]), 2),
            "williams_r":    round(float(latest["williams_r"]), 2),
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None


# ============================================================
# ALIGNMENT SCORE
# ============================================================
def calculate_alignment_score(indicators: dict) -> dict:
    try:
        wr     = indicators.get("williams_r", -50.0)
        srsi_k = indicators.get("stochrsi_k", 50.0)

        wr_bias   = "BULLISH" if wr <= -80 else ("BEARISH" if wr >= -20 else "NEUTRAL")
        srsi_bias = "BULLISH" if srsi_k <= 20 else ("BEARISH" if srsi_k >= 80 else "NEUTRAL")

        if wr_bias == "NEUTRAL" and srsi_bias == "NEUTRAL":
            return {"alignment_score": 50.0, "confidence_boost": 0.0,
                    "wr_bias": wr_bias, "srsi_bias": srsi_bias, "aligned": False}
        if wr_bias == srsi_bias and wr_bias != "NEUTRAL":
            return {"alignment_score": 100.0, "confidence_boost": 20.0,
                    "wr_bias": wr_bias, "srsi_bias": srsi_bias, "aligned": True}
        if wr_bias != srsi_bias and "NEUTRAL" not in (wr_bias, srsi_bias):
            return {"alignment_score": 0.0, "confidence_boost": 0.0,
                    "wr_bias": wr_bias, "srsi_bias": srsi_bias, "aligned": False}
        return {"alignment_score": 50.0, "confidence_boost": 5.0,
                "wr_bias": wr_bias, "srsi_bias": srsi_bias, "aligned": False}
    except Exception as e:
        logger.error(f"Alignment score error: {e}")
        return {"alignment_score": 50.0, "confidence_boost": 0.0,
                "wr_bias": "NEUTRAL", "srsi_bias": "NEUTRAL", "aligned": False}


# ============================================================
# H4 TREND CHECK
# ============================================================
async def check_h4_trend(pair: str) -> dict:
    try:
        df = await get_price_data(pair, interval="4h", outputsize=60)
        if df is None or len(df) < 51:
            return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}
        ma50          = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        latest_close  = float(df["close"].iloc[-1])
        latest_ma50   = float(ma50.iloc[-1])
        h4_trend      = "BULLISH" if latest_close > latest_ma50 else "BEARISH"
        return {
            "h4_trend":    h4_trend,
            "buy_allowed": h4_trend == "BULLISH",
            "sell_allowed":h4_trend == "BEARISH",
        }
    except Exception as e:
        logger.error(f"H4 trend check error for {pair}: {e}")
        return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}


# ============================================================
# DXY CORRELATION
# ============================================================
async def check_dxy_correlation() -> dict:
    try:
        df = await get_generic_price_data("DXY", interval="1h", outputsize=30)
        if df is None or len(df) < 21:
            return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}
        ma20         = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        latest_close = float(df["close"].iloc[-1])
        latest_ma20  = float(ma20.iloc[-1])
        if latest_close > latest_ma20 * 1.002:
            dxy_trend, buy_allowed = "UPTREND", False
        elif latest_close < latest_ma20 * 0.998:
            dxy_trend, buy_allowed = "DOWNTREND", True
        else:
            dxy_trend, buy_allowed = "NEUTRAL", True
        return {
            "dxy_trend":  dxy_trend,
            "buy_allowed":buy_allowed,
            "dxy_ma20":   round(latest_ma20, 3),
            "dxy_price":  round(latest_close, 3),
        }
    except Exception as e:
        logger.error(f"DXY correlation check error: {e}")
        return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}


# ============================================================
# NEWS GUARD
# ============================================================
async def check_news_impact(symbol: str = "XAU/USD") -> dict:
    try:
        url = (
            f"https://api.twelvedata.com/news"
            f"?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}&outputsize=20"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        articles = data.get("data", []) if isinstance(data, dict) else []
        if not articles:
            return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}

        now_utc = datetime.now(timezone.utc)
        nearest_event, min_minutes = None, None

        for article in articles:
            pub_str = article.get("datetime") or article.get("published_at") or ""
            if not pub_str:
                continue
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                diff = abs((pub_dt - now_utc).total_seconds() / 60)
                if diff <= 60 and (min_minutes is None or diff < min_minutes):
                    min_minutes = int(diff)
                    nearest_event = article.get("title", "Unknown news event")
            except Exception:
                continue

        news_nearby = min_minutes is not None
        return {
            "news_nearby":    news_nearby,
            "signal_allowed": not news_nearby,
            "nearest_event":  nearest_event,
            "minutes_away":   min_minutes,
        }
    except Exception as e:
        logger.error(f"News impact check error: {e}")
        return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}


# ============================================================
# WEIGHTED CONFIDENCE
# ============================================================
def calculate_weighted_confidence(indicators: dict, alignment: dict) -> dict:
    try:
        trend_pts = 0
        if indicators["trend"] == "BULLISH":
            trend_pts += 50
        if indicators["adx"] > 25:
            trend_pts += 30
        elif indicators["adx"] > 20:
            trend_pts += 15
        if indicators["macd"] > indicators["macd_signal"]:
            trend_pts += 20
        trend_score = min(trend_pts, 100)

        momentum_pts = 0
        rsi = indicators["rsi"]
        if 40 <= rsi <= 60:
            momentum_pts += 40
        elif 30 <= rsi < 40 or 60 < rsi <= 70:
            momentum_pts += 60
        elif rsi < 30 or rsi > 70:
            momentum_pts += 20
        macd_hist = indicators["macd"] - indicators["macd_signal"]
        if macd_hist > 0:
            momentum_pts += 30
        elif macd_hist > -0.5:
            momentum_pts += 15
        cci = indicators["cci"]
        if -100 <= cci <= 100:
            momentum_pts += 30
        elif -200 <= cci <= 200:
            momentum_pts += 15
        momentum_score = min(momentum_pts, 100)

        trigger_pts = 0
        stoch_k = indicators["stoch_k"]
        wr      = indicators["williams_r"]
        if 20 < stoch_k < 80:
            trigger_pts += 40
        else:
            trigger_pts += 20
        if -80 < wr < -20:
            trigger_pts += 40
        else:
            trigger_pts += 20
        trigger_pts += alignment["confidence_boost"]
        trigger_score = min(trigger_pts, 100)

        weighted_score = (
            trend_score    * 0.40
            + momentum_score * 0.30
            + trigger_score  * 0.30
        )
        weighted_score = round(weighted_score, 1)
        conviction_level = "HIGH" if weighted_score >= 85 else ("MEDIUM" if weighted_score >= 60 else "LOW")

        return {
            "trend_score":     round(trend_score, 1),
            "momentum_score":  round(momentum_score, 1),
            "trigger_score":   round(trigger_score, 1),
            "weighted_score":  weighted_score,
            "conviction_level":conviction_level,
        }
    except Exception as e:
        logger.error(f"Weighted confidence error: {e}")
        return {"trend_score": 50.0, "momentum_score": 50.0, "trigger_score": 50.0,
                "weighted_score": 50.0, "conviction_level": "LOW"}


# ============================================================
# CANDLESTICK PATTERNS
# ============================================================
def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 2:
            return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
        c1 = df.iloc[-2]
        c0 = df.iloc[-1]
        o1, h1, l1, cl1 = float(c1["open"]), float(c1["high"]), float(c1["low"]), float(c1["close"])
        o0, h0, l0, cl0 = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])
        body0  = abs(cl0 - o0)
        range0 = h0 - l0 if h0 != l0 else 1e-9
        if body0 / range0 < 0.1:
            return {"pattern": "DOJI", "pattern_strength": "MODERATE", "bullish": None}
        if (cl1 < o1) and (cl0 > o0) and (cl0 > o1) and (o0 < cl1):
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": True}
        if (cl1 > o1) and (cl0 < o0) and (cl0 < o1) and (o0 > cl1):
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": False}
        upper_wick = h0 - max(o0, cl0)
        lower_wick = min(o0, cl0) - l0
        if lower_wick >= 2 * body0 and upper_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": True}
        if upper_wick >= 2 * body0 and lower_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": False}
        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
    except Exception as e:
        logger.error(f"Candlestick pattern detection error: {e}")
        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}


# ============================================================
# SAFETY SWITCH
# ============================================================
def apply_safety_switch(signal_type: str, indicators: dict, alignment: dict, weighted: dict) -> dict:
    try:
        trend_ok    = weighted["trend_score"] >= 60
        momentum_ok = weighted["momentum_score"] >= 60
        stoch_k     = indicators["stoch_k"]
        wr          = indicators["williams_r"]
        if signal_type == "SELL" and trend_ok and momentum_ok and (stoch_k <= 20 or wr <= -80):
            return {"signal_allowed": False,
                    "reason": "Safety switch: SELL signal but triggers oversold — waiting for pullback"}
        if signal_type == "BUY" and trend_ok and momentum_ok and (stoch_k >= 80 or wr >= -20):
            return {"signal_allowed": False,
                    "reason": "Safety switch: BUY signal but triggers overbought — waiting for dip"}
        return {"signal_allowed": True, "reason": "Safety switch: clear"}
    except Exception as e:
        logger.error(f"Safety switch error: {e}")
        return {"signal_allowed": True, "reason": "Safety switch: error (pass-through)"}


# ============================================================
# AI ANALYSIS
# ============================================================
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    try:
        alignment   = calculate_alignment_score(indicators)
        h4_data     = await check_h4_trend(symbol)
        dxy_data    = await check_dxy_correlation()
        td_symbol   = GOLD_PAIRS.get(symbol, {}).get("twelve_data_symbol", "XAU/USD")
        news_data   = await check_news_impact(td_symbol)
        weighted    = calculate_weighted_confidence(indicators, alignment)
        df_h1       = await get_price_data(symbol, interval="1h", outputsize=10)
        pattern_data= detect_candlestick_patterns(df_h1) if df_h1 is not None else \
                      {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}

        dp          = params["decimal_places"]
        dxy_label   = "NEUTRAL" if dxy_data["dxy_trend"] in ("NEUTRAL", "UNKNOWN") else dxy_data["dxy_trend"]
        news_label  = "BLOCKED ⚠️" if news_data["news_nearby"] else "Clear ✅"
        pattern_label = (
            f"{pattern_data['pattern']} "
            f"({'Bullish' if pattern_data['bullish'] else 'Bearish' if pattern_data['bullish'] is False else 'Neutral'}) "
            f"[{pattern_data['pattern_strength']}]"
        )

        system_message = (
            "You are an elite institutional gold trader. "
            "Provide precise, actionable trading signals with strict risk management. "
            "Consider all provided multi-indicator context carefully."
        )

        prompt = f"""
Analyze {symbol} market data and provide a professional trading signal.

=== CORE MARKET DATA (H1) ===
Current Price : {indicators['current_price']}
Trend (MA50)  : {indicators['trend']}
ATR(14)       : {indicators['atr']:.{dp}f}
BB Upper/Lower: {indicators['bb_upper']:.{dp}f} / {indicators['bb_lower']:.{dp}f}

=== EXISTING INDICATORS ===
RSI(14)       : {indicators['rsi']:.2f}
MACD          : {indicators['macd']:.6f}  |  Signal: {indicators['macd_signal']:.6f}
MA20 / MA50   : {indicators['ma_20']:.{dp}f} / {indicators['ma_50']:.{dp}f}

=== ADVANCED INDICATORS ===
ADX(14)       : {indicators['adx']:.2f}  {'(TRENDING)' if indicators['adx'] > 25 else '(WEAK TREND)'}
Stochastic(9,6): K={indicators['stoch_k']:.2f}  D={indicators['stoch_d']:.2f}
StochRSI(14)  : K={indicators['stochrsi_k']:.2f}  D={indicators['stochrsi_d']:.2f}
CCI(14)       : {indicators['cci']:.2f}
Williams%R(14): {indicators['williams_r']:.2f}

=== ALIGNMENT SCORE (Williams%R + StochRSI) ===
Alignment     : {alignment['alignment_score']:.0f}%  ({'ALIGNED ✅' if alignment['aligned'] else 'DIVERGED ⚠️'})
Williams%R    : {alignment['wr_bias']}
StochRSI      : {alignment['srsi_bias']}
Confidence Boost: +{alignment['confidence_boost']:.0f}pts

=== MULTI-TIMEFRAME (H4) ===
H4 Trend      : {h4_data['h4_trend']}
BUY Allowed   : {'YES' if h4_data['buy_allowed'] else 'NO — H4 BEARISH'}
SELL Allowed  : {'YES' if h4_data['sell_allowed'] else 'NO — H4 BULLISH'}

=== DXY CORRELATION ===
DXY Trend     : {dxy_label}
BUY Allowed   : {'YES' if dxy_data['buy_allowed'] else 'NO — DXY UPTREND (inverse pressure)'}

=== NEWS IMPACT ===
Status        : {news_label}
{f"Nearest Event : {news_data['nearest_event']} ({news_data['minutes_away']} min away)" if news_data['news_nearby'] else "No high-impact events within ±60 min"}

=== WEIGHTED CONFIDENCE (40/30/30) ===
Trend Score   : {weighted['trend_score']:.1f}/100  (weight 40%)
Momentum Score: {weighted['momentum_score']:.1f}/100  (weight 30%)
Trigger Score : {weighted['trigger_score']:.1f}/100  (weight 30%)
TOTAL SCORE   : {weighted['weighted_score']:.1f}/100  → {weighted['conviction_level']} CONVICTION

=== PRICE ACTION ===
Pattern       : {pattern_label}

=== ATR MULTIPLIERS ===
SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}
Min R:R: {params['min_rr']}

=== OUTPUT FORMAT (JSON ONLY) ===
{{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
"""

        ai_response = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user",   "content": prompt},
                    ],
                    api_key=OPENAI_API_KEY,
                )
                ai_response = response.choices[0].message.content
                if ai_response and len(ai_response.strip()) > 10:
                    break
            except Exception as e:
                logger.warning(f"LLM attempt {attempt+1}/3 for {symbol}: {e}")
                await asyncio.sleep(1)

        if not ai_response or len(ai_response.strip()) < 10:
            return None

        raw = ai_response.strip()
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()
        if not raw.startswith('{'):
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)

        ai_data = None
        for parse_attempt in range(3):
            try:
                if parse_attempt == 0:
                    ai_data = json.loads(raw)
                elif parse_attempt == 1:
                    fixed = re.sub(r',\s*}', '}', raw)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
                    fixed = re.sub(r'(\d)\s*\n\s*"', r'\1,\n"', fixed)
                    fixed = fixed.replace("'", '"')
                    ai_data = json.loads(fixed)
                else:
                    signal_m   = re.search(r'"signal"\s*:\s*"(\w+)"', raw)
                    conf_m     = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                    entry_m    = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                    analysis_m = re.search(r'"analysis"\s*:\s*"([^"]*)"', raw)
                    ai_data = {
                        "signal":      signal_m.group(1) if signal_m else "NEUTRAL",
                        "confidence":  float(conf_m.group(1)) if conf_m else 50.0,
                        "entry_price": float(entry_m.group(1)) if entry_m else indicators["current_price"],
                        "analysis":    analysis_m.group(1) if analysis_m else "AI analysis unavailable",
                        "tp_levels":   [],
                        "sl_price":    0,
                    }
                break
            except Exception:
                if parse_attempt == 2:
                    logger.warning(f"All JSON parsing failed for {symbol}")

        if not ai_data:
            return None

        entry       = ai_data.get("entry_price", indicators["current_price"])
        signal_type = ai_data.get("signal", "NEUTRAL")
        tp_levels   = ai_data.get("tp_levels", [])
        atr         = indicators["atr"]
        dp          = params["decimal_places"]

        if signal_type != "NEUTRAL" and (len(tp_levels) != 3 or len(set(tp_levels)) != 3):
            if signal_type == "BUY":
                tp_levels = [
                    round(entry + atr * params["atr_multiplier_tp1"], dp),
                    round(entry + atr * params["atr_multiplier_tp2"], dp),
                    round(entry + atr * params["atr_multiplier_tp3"], dp),
                ]
            else:
                tp_levels = [
                    round(entry - atr * params["atr_multiplier_tp1"], dp),
                    round(entry - atr * params["atr_multiplier_tp2"], dp),
                    round(entry - atr * params["atr_multiplier_tp3"], dp),
                ]
            ai_data["tp_levels"] = tp_levels

        sl_price = ai_data.get("sl_price", 0)
        if signal_type == "BUY" and (sl_price >= entry or sl_price == 0):
            sl_price = round(entry - atr * params["atr_multiplier_sl"], dp)
        elif signal_type == "SELL" and (sl_price <= entry or sl_price == 0):
            sl_price = round(entry + atr * params["atr_multiplier_sl"], dp)
        ai_data["sl_price"] = sl_price

        risk_reward = ai_data.get("risk_reward", params["min_rr"])
        if not isinstance(risk_reward, (int, float)):
            risk_reward = params["min_rr"]
        ai_data["risk_reward"] = risk_reward

        ai_data["_alignment"] = alignment
        ai_data["_h4"]        = h4_data
        ai_data["_dxy"]       = dxy_data
        ai_data["_news"]      = news_data
        ai_data["_weighted"]  = weighted
        ai_data["_pattern"]   = pattern_data

        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis for {symbol}: {e}")
        return None


# ============================================================
# TELEGRAM SIGNAL SENDER
# ============================================================
def sanitize_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_signal_to_telegram(
    pair, signal_type, entry_price, tp_levels, sl_price,
    confidence, risk_reward, analysis,
    conviction_level="MEDIUM",
    alignment_score=50.0,
    technical_score=50.0,
    h4_trend="UNKNOWN",
    dxy_status="NEUTRAL",
    news_status="Clear",
    kelly_pct=1.0,
    volatility_class="MEDIUM",
    hurst=0.5,
    session_name="LONDON",
):
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        signal_emoji   = "🟢" if signal_type == "BUY" else "🔴"
        action         = signal_type.capitalize()
        conviction_tag = " [HIGH CONVICTION 🔥]" if conviction_level == "HIGH" else ""
        entry_lo       = round(entry_price - 0.50, 2)
        entry_hi       = round(entry_price + 0.50, 2)

        copier_message = (
            f"{signal_emoji} #{pair} [SWING]{conviction_tag}\n"
            f"\n"
            f"{action} {entry_lo} - {entry_hi}\n"
            f"\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n"
            f"\n"
            f"SL: {sl_price}\n"
        )

        safe_analysis = sanitize_html(analysis)
        regime_label  = "TRENDING 📈" if hurst > 0.6 else ("MEAN-REV 🔄" if hurst < 0.4 else "NEUTRAL ↔️")
        info_message  = (
            f"<b>📊 R:R:</b> 1:{risk_reward}  "
            f"<b>⚡ AI Confidence:</b> {confidence}%\n"
            f"<b>🎯 Technical Score:</b> {technical_score:.0f}/100  "
            f"<b>🔗 Alignment:</b> {alignment_score:.0f}% (W%R + StochRSI)\n"
            f"<b>📈 H4 Trend:</b> {h4_trend}  "
            f"<b>💵 DXY:</b> {dxy_status}  "
            f"<b>📰 News:</b> {news_status}\n"
            f"<b>🧬 DNA:</b> {volatility_class}  "
            f"<b>📐 Hurst:</b> {hurst:.2f} ({regime_label})  "
            f"<b>💰 Kelly:</b> {kelly_pct:.2f}% risk\n"
            f"<b>🕐 Session:</b> {session_name}\n"
            f"<b>📝</b> {safe_analysis}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold A+ Elite Edition</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=copier_message)
        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=info_message, parse_mode="HTML")
        logger.info(f"✅ Gold signal sent to {TELEGRAM_GOLD_CHANNEL_ID}: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Error sending gold signal to Telegram: {e}")


# ============================================================
# SIGNAL GENERATION — 20-GATE PIPELINE
# ============================================================
async def generate_gold_signal(pair: str):
    try:
        # ── Kill switch guard ────────────────────────────────────────────────
        if is_kill_switch_active():
            logger.warning(f"🚨 Kill switch active — skipping {pair}")
            return

        params = GOLD_PAIRS[pair]
        logger.info(f"📊 Generating gold signal for {pair}")

        # ── Fetch H1 data ────────────────────────────────────────────────────
        df = await get_price_data(pair, interval="1h", outputsize=100)
        if df is None or len(df) < 60:
            logger.warning(f"Insufficient data for {pair}")
            return

        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        # ── Update DNA ───────────────────────────────────────────────────────
        dna = _dna[pair]
        if not dna._loaded:
            await dna.load()
        dna.update_atr(indicators["atr"])
        await dna.save()

        # ── Advanced indicators ──────────────────────────────────────────────
        hurst   = calculate_hurst_exponent(df["close"].tail(50))
        entropy = calculate_shannon_entropy(df["close"].tail(50))
        kc      = calculate_keltner_channels(df)
        sweep   = detect_liquidity_sweep(df)
        vp      = calculate_volume_profile(df)
        gsr     = await get_gold_silver_ratio()

        # ── Dynamic ATR settings from DNA + Hurst ───────────────────────────
        dyn_atr = get_dynamic_atr_settings(dna.volatility_class, hurst)
        params  = {**params, **{
            "atr_multiplier_sl":  dyn_atr["sl"],
            "atr_multiplier_tp1": dyn_atr["tp1"],
            "atr_multiplier_tp2": dyn_atr["tp2"],
            "atr_multiplier_tp3": dyn_atr["tp3"],
        }}

        # ── AI analysis ──────────────────────────────────────────────────────
        ai_analysis = await generate_ai_analysis(pair, indicators, params)
        if not ai_analysis:
            return

        signal_type = ai_analysis.get("signal", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL)")
            return

        alignment    = ai_analysis.get("_alignment", {})
        h4_data      = ai_analysis.get("_h4", {})
        dxy_data     = ai_analysis.get("_dxy", {})
        news_data    = ai_analysis.get("_news", {})
        weighted     = ai_analysis.get("_weighted", {})
        pattern_data = ai_analysis.get("_pattern", {})
        w_score      = weighted.get("weighted_score", 50.0)
        session_name, session_min_score = get_current_session()

        # ── GATE 01: News Guard ──────────────────────────────────────────────
        if GATES["gate_01_news_guard"] and not news_data.get("signal_allowed", True):
            reason = f"News guard: '{news_data.get('nearest_event', 'unknown')}' ({news_data.get('minutes_away', '?')} min away)"
            _gate_blocked(pair, "gate_01_news_guard", reason, indicators, w_score)
            logger.info(f"{pair} BLOCKED — Gate 01: {reason}")
            return

        # ── GATE 02: H4 MTF Alignment ────────────────────────────────────────
        if GATES["gate_02_h4_mtf"]:
            if signal_type == "BUY" and not h4_data.get("buy_allowed", True):
                reason = "H4 trend is BEARISH (MTF conflict)"
                _gate_blocked(pair, "gate_02_h4_mtf", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 02: {reason}")
                return
            if signal_type == "SELL" and not h4_data.get("sell_allowed", True):
                reason = "H4 trend is BULLISH (MTF conflict)"
                _gate_blocked(pair, "gate_02_h4_mtf", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 02: {reason}")
                return

        # ── GATE 03: DXY Correlation ─────────────────────────────────────────
        if GATES["gate_03_dxy_correlation"]:
            if signal_type == "BUY" and not dxy_data.get("buy_allowed", True):
                reason = "DXY in strong uptrend (inverse correlation)"
                _gate_blocked(pair, "gate_03_dxy_correlation", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 03: {reason}")
                return

        # ── GATE 04: Candlestick Price Action ────────────────────────────────
        if GATES["gate_04_candlestick_pa"]:
            if pattern_data.get("pattern") == "DOJI":
                reason = "DOJI candle — indecision, no clear direction"
                _gate_blocked(pair, "gate_04_candlestick_pa", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 04: {reason}")
                return

        # ── GATE 05: Choppy Market Guard ─────────────────────────────────────
        if GATES["gate_05_choppy_market"]:
            bb_width = indicators.get("bb_width", 1.0)
            if indicators["adx"] < 20 and bb_width < 0.8:
                reason = f"Choppy market: ADX={indicators['adx']:.1f} < 20 & BB width={bb_width:.3f}% < 0.8%"
                _gate_blocked(pair, "gate_05_choppy_market", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 05: {reason}")
                return

        # ── GATE 06: Circuit Breaker (flash crash) ───────────────────────────
        if GATES["gate_06_circuit_breaker"]:
            recent_closes = df["close"].tail(5).values
            max_move = max(abs(recent_closes[i] - recent_closes[i-1]) for i in range(1, len(recent_closes)))
            if max_move > indicators["atr"] * 3:
                reason = f"Flash crash detected: max 1-bar move={max_move:.2f} > 3×ATR={indicators['atr']*3:.2f}"
                _gate_blocked(pair, "gate_06_circuit_breaker", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 06: {reason}")
                return

        # ── GATE 07: Session Confidence Filter ───────────────────────────────
        if GATES["gate_07_session_confidence"]:
            if w_score < session_min_score:
                reason = f"Session {session_name}: score {w_score:.1f} < min {session_min_score}"
                _gate_blocked(pair, "gate_07_session_confidence", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 07: {reason}")
                return

        # ── GATE 08: Shannon Entropy ─────────────────────────────────────────
        if GATES["gate_08_shannon_entropy"]:
            if entropy > 3.5:
                reason = f"Shannon entropy too high: {entropy:.3f} > 3.5 (chaotic market)"
                _gate_blocked(pair, "gate_08_shannon_entropy", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 08: {reason}")
                return

        # ── GATE 09: Hurst Exponent ──────────────────────────────────────────
        if GATES["gate_09_hurst_exponent"]:
            if signal_type == "BUY" and hurst < 0.4:
                reason = f"Hurst={hurst:.3f} < 0.4 — mean-reverting regime, BUY momentum unreliable"
                _gate_blocked(pair, "gate_09_hurst_exponent", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 09: {reason}")
                return
            if signal_type == "SELL" and hurst < 0.4:
                reason = f"Hurst={hurst:.3f} < 0.4 — mean-reverting regime, SELL momentum unreliable"
                _gate_blocked(pair, "gate_09_hurst_exponent", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 09: {reason}")
                return

        # ── GATE 10: Keltner Channel Overextension ───────────────────────────
        if GATES["gate_10_keltner_channel"]:
            if signal_type == "BUY" and kc.get("price_above"):
                reason = f"Price above Keltner upper ({kc['kc_upper']:.2f}) — overextended BUY"
                _gate_blocked(pair, "gate_10_keltner_channel", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 10: {reason}")
                return
            if signal_type == "SELL" and kc.get("price_below"):
                reason = f"Price below Keltner lower ({kc['kc_lower']:.2f}) — overextended SELL"
                _gate_blocked(pair, "gate_10_keltner_channel", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 10: {reason}")
                return

        # ── GATE 11: OBV Divergence — DISABLED (no real volume) ─────────────
        # gate_11_obv_divergence is False by default

        # ── GATE 12: Liquidity Sweep Detection ──────────────────────────────
        if GATES["gate_12_liquidity_sweep"]:
            if sweep.get("sweep_detected"):
                sweep_type = sweep.get("sweep_type", "")
                # Bearish sweep → potential SELL setup (don't block SELL)
                # Bullish sweep → potential BUY setup (don't block BUY)
                if signal_type == "BUY" and sweep_type == "BEARISH_SWEEP":
                    reason = "Bearish liquidity sweep detected — stop hunt risk for BUY"
                    _gate_blocked(pair, "gate_12_liquidity_sweep", reason, indicators, w_score)
                    logger.info(f"{pair} BLOCKED — Gate 12: {reason}")
                    return
                if signal_type == "SELL" and sweep_type == "BULLISH_SWEEP":
                    reason = "Bullish liquidity sweep detected — stop hunt risk for SELL"
                    _gate_blocked(pair, "gate_12_liquidity_sweep", reason, indicators, w_score)
                    logger.info(f"{pair} BLOCKED — Gate 12: {reason}")
                    return

        # ── GATE 13: Order Block Strength ────────────────────────────────────
        if GATES["gate_13_order_block"]:
            # Proxy: require ADX > 18 for order block validity
            if indicators["adx"] < 18:
                reason = f"Order block invalid: ADX={indicators['adx']:.1f} < 18 (no institutional momentum)"
                _gate_blocked(pair, "gate_13_order_block", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 13: {reason}")
                return

        # ── GATE 14: Gold-Silver Ratio Filter ────────────────────────────────
        if GATES["gate_14_gold_silver_ratio"] and gsr is not None:
            if gsr > 95 and signal_type == "BUY":
                reason = f"GSR={gsr:.1f} > 95 — gold extremely overvalued vs silver, BUY risk elevated"
                _gate_blocked(pair, "gate_14_gold_silver_ratio", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 14: {reason}")
                return
            if gsr < 55 and signal_type == "SELL":
                reason = f"GSR={gsr:.1f} < 55 — gold extremely undervalued vs silver, SELL risk elevated"
                _gate_blocked(pair, "gate_14_gold_silver_ratio", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 14: {reason}")
                return

        # ── GATE 15: Volume Weighted MACD — DISABLED (no real volume) ────────
        # gate_15_vw_macd is False by default

        # ── GATE 16: Daily Drawdown Protection ───────────────────────────────
        if GATES["gate_16_daily_drawdown"]:
            today_losses = get_today_losses()
            loss_count   = len(today_losses)
            loss_pips    = sum(abs(x["pips"]) for x in today_losses)
            if loss_count >= 2 or loss_pips >= 40:
                reason = f"Daily drawdown limit: {loss_count} losses, {loss_pips:.1f} pips today"
                _gate_blocked(pair, "gate_16_daily_drawdown", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 16: {reason}")
                return

        # ── GATE 17: Signal Throttle (6h minimum) ────────────────────────────
        if GATES["gate_17_signal_throttle"]:
            last_ts = _last_signal_ts.get(pair)
            if last_ts:
                elapsed_h = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
                if elapsed_h < 6:
                    reason = f"Signal throttle: only {elapsed_h:.1f}h since last signal (min 6h)"
                    _gate_blocked(pair, "gate_17_signal_throttle", reason, indicators, w_score)
                    logger.info(f"{pair} BLOCKED — Gate 17: {reason}")
                    return

        # ── GATE 18: Volume Profile POC Confirmation ─────────────────────────
        if GATES["gate_18_volume_profile_poc"]:
            poc = vp.get("poc", 0)
            price = indicators["current_price"]
            poc_distance_pct = abs(price - poc) / price * 100 if price > 0 else 100
            if poc_distance_pct > 2.0:
                reason = f"Price {price:.2f} too far from POC {poc:.2f} ({poc_distance_pct:.2f}% > 2%)"
                _gate_blocked(pair, "gate_18_volume_profile_poc", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 18: {reason}")
                return

        # ── GATE 19: Kelly Criterion ─────────────────────────────────────────
        kelly_data = {"kelly_pct": 1.0, "win_rate": 0.5, "avg_win": 10.0, "avg_loss": 10.0}
        if GATES["gate_19_kelly_criterion"]:
            kelly_data = await calculate_kelly_fraction(pair)
            if kelly_data["kelly_pct"] <= 0:
                reason = f"Kelly fraction ≤ 0 — negative edge, skip trade"
                _gate_blocked(pair, "gate_19_kelly_criterion", reason, indicators, w_score)
                logger.info(f"{pair} BLOCKED — Gate 19: {reason}")
                return

        # ── GATE 20: Correlation Matrix Monitor ──────────────────────────────
        # (auto-managed by monitor_correlation_matrix scheduler job)
        # Gate 03 is auto-toggled; Gate 20 itself is a meta-gate (no block here)

        # ── Safety switch ────────────────────────────────────────────────────
        safety = apply_safety_switch(signal_type, indicators, alignment, weighted)
        if not safety["signal_allowed"]:
            _gate_blocked(pair, "safety_switch", safety["reason"], indicators, w_score)
            logger.info(f"{pair} BLOCKED — Safety switch: {safety['reason']}")
            return

        # ── Confidence gate ──────────────────────────────────────────────────
        confidence = float(ai_analysis.get("confidence", 0))
        if confidence < GOLD_PAIRS[pair]["min_confidence"]:
            reason = f"AI confidence {confidence}% < {GOLD_PAIRS[pair]['min_confidence']}%"
            _gate_blocked(pair, "confidence_gate", reason, indicators, w_score)
            logger.info(f"{pair} BLOCKED — {reason}")
            return

        # ── All gates passed — build signal ──────────────────────────────────
        entry_price      = ai_analysis["entry_price"]
        tp_levels        = ai_analysis["tp_levels"]
        sl_price         = ai_analysis["sl_price"]
        risk_reward      = ai_analysis.get("risk_reward", params["min_rr"])
        conviction_level = weighted.get("conviction_level", "MEDIUM")
        dxy_trend        = dxy_data.get("dxy_trend", "UNKNOWN")
        dxy_status       = "CONFLICT ⚠️" if dxy_trend == "UPTREND" and signal_type == "BUY" else dxy_trend
        news_status      = "BLOCKED ⚠️" if news_data.get("news_nearby") else "Clear ✅"
        h4_trend         = h4_data.get("h4_trend", "UNKNOWN")

        # ── Blackbox: PASSED event ───────────────────────────────────────────
        _blackbox_log({
            "event":       "SIGNAL_PASSED",
            "pair":        pair,
            "signal_type": signal_type,
            "entry_price": entry_price,
            "tp_levels":   tp_levels,
            "sl_price":    sl_price,
            "confidence":  confidence,
            "score":       w_score,
            "kelly_pct":   kelly_data["kelly_pct"],
            "hurst":       hurst,
            "entropy":     entropy,
            "session":     session_name,
            "indicators":  indicators,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        })

        # ── Store in DB ──────────────────────────────────────────────────────
        signal_doc = {
            "pair":             pair,
            "type":             signal_type,
            "entry_price":      entry_price,
            "current_price":    indicators["current_price"],
            "tp_levels":        tp_levels,
            "sl_price":         sl_price,
            "confidence":       round(confidence, 1),
            "analysis":         ai_analysis.get("analysis", ""),
            "risk_reward":      risk_reward,
            "timeframe":        "H1",
            "status":           "ACTIVE",
            "created_at":       datetime.now(timezone.utc),
            "conviction_level": conviction_level,
            "weighted_score":   w_score,
            "alignment_score":  alignment.get("alignment_score", 50.0),
            "h4_trend":         h4_trend,
            "dxy_trend":        dxy_trend,
            "adx":              indicators.get("adx"),
            "stoch_k":          indicators.get("stoch_k"),
            "williams_r":       indicators.get("williams_r"),
            "cci":              indicators.get("cci"),
            "pattern":          pattern_data.get("pattern", "NONE"),
            "hurst":            hurst,
            "entropy":          entropy,
            "volatility_class": dna.volatility_class,
            "kelly_pct":        kelly_data["kelly_pct"],
            "session":          session_name,
            "breakeven_set":    False,
        }
        await db.gold_signals.insert_one(signal_doc)

        # ── Update throttle timestamp ────────────────────────────────────────
        _last_signal_ts[pair] = datetime.now(timezone.utc)

        # ── Send to Telegram ─────────────────────────────────────────────────
        await send_signal_to_telegram(
            pair=pair,
            signal_type=signal_type,
            entry_price=entry_price,
            tp_levels=tp_levels,
            sl_price=sl_price,
            confidence=round(confidence, 1),
            risk_reward=risk_reward,
            analysis=ai_analysis.get("analysis", ""),
            conviction_level=conviction_level,
            alignment_score=alignment.get("alignment_score", 50.0),
            technical_score=w_score,
            h4_trend=h4_trend,
            dxy_status=dxy_status,
            news_status=news_status,
            kelly_pct=kelly_data["kelly_pct"],
            volatility_class=dna.volatility_class,
            hurst=hurst,
            session_name=session_name,
        )

        logger.info(
            f"✅ {pair} {signal_type} @ {entry_price} | TP: {tp_levels} | SL: {sl_price} | "
            f"Conf: {confidence}% | Score: {w_score:.1f} | Conviction: {conviction_level} | "
            f"Kelly: {kelly_data['kelly_pct']:.2f}% | Hurst: {hurst:.3f} | Session: {session_name}"
        )
    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")


async def run_gold_signals():
    logger.info("🥇 Running gold signal generation...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(2)
    logger.info("🥇 Gold signal generation complete")


# ============================================================
# DNA WEEKLY UPDATER
# ============================================================
async def update_dna_weekly():
    """Monday 00:05 UTC — refresh DNA from fresh H1 data."""
    logger.info("🧬 Weekly DNA update starting...")
    for pair in GOLD_PAIRS:
        df = await get_price_data(pair, interval="1h", outputsize=100)
        if df is not None and len(df) >= 14:
            atr_series = ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            new_atr = float(atr_series.iloc[-1])
            _dna[pair].update_atr(new_atr)
            await _dna[pair].save()
            logger.info(f"🧬 DNA updated for {pair}: ATR={new_atr:.2f} | Vol={_dna[pair].volatility_class}")


# ============================================================
# FRIDAY VOLATILITY SNAPSHOT
# ============================================================
async def friday_volatility_snapshot():
    """Friday 21:00 UTC — log volatility state before weekend."""
    logger.info("📸 Friday volatility snapshot...")
    for pair in GOLD_PAIRS:
        snap = _dna[pair].snapshot()
        await db.volatility_snapshots.insert_one({
            **snap,
            "snapshot_type": "friday_close",
            "timestamp":     datetime.now(timezone.utc),
        })
        logger.info(f"📸 {pair} snapshot: {snap}")


# ============================================================
# OUTCOME TRACKER (Gate 16 feedback)
# ============================================================
async def track_signal_outcomes():
    """Every 60s — check active signals against live prices."""
    try:
        active = await db.gold_signals.find({"status": "ACTIVE"}).to_list(100)
        for sig in active:
            pair  = sig.get("pair")
            stype = sig.get("type", "").upper()
            entry = sig.get("entry_price", 0)
            sl    = sig.get("sl_price", 0)
            tps   = sig.get("tp_levels", [])
            if not tps:
                continue

            price = await _get_live_price(pair)
            if price is None:
                continue

            outcome = None
            if stype == "BUY":
                if price <= sl:
                    pips = round((price - entry) / 0.1, 1)
                    outcome = {"status": "CLOSED_SL", "result": "LOSS", "pips": pips}
                elif len(tps) >= 3 and price >= tps[2]:
                    pips = round((price - entry) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP3", "result": "WIN", "pips": pips}
                elif len(tps) >= 2 and price >= tps[1]:
                    pips = round((price - entry) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP2", "result": "WIN", "pips": pips}
                elif len(tps) >= 1 and price >= tps[0]:
                    pips = round((price - entry) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP1", "result": "WIN", "pips": pips}
            elif stype == "SELL":
                if price >= sl:
                    pips = round((entry - price) / 0.1, 1)
                    outcome = {"status": "CLOSED_SL", "result": "LOSS", "pips": pips}
                elif len(tps) >= 3 and price <= tps[2]:
                    pips = round((entry - price) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP3", "result": "WIN", "pips": pips}
                elif len(tps) >= 2 and price <= tps[1]:
                    pips = round((entry - price) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP2", "result": "WIN", "pips": pips}
                elif len(tps) >= 1 and price <= tps[0]:
                    pips = round((entry - price) / 0.1, 1)
                    outcome = {"status": "CLOSED_TP1", "result": "WIN", "pips": pips}

            if outcome:
                await db.gold_signals.update_one(
                    {"_id": sig["_id"]},
                    {"$set": {
                        "status":    outcome["status"],
                        "result":    outcome["result"],
                        "pips":      outcome["pips"],
                        "exit_price":price,
                        "closed_at": datetime.now(timezone.utc),
                    }},
                )
                if outcome["result"] == "LOSS":
                    record_daily_loss(pair, outcome["pips"])
                logger.info(f"📋 {pair} signal closed: {outcome['status']} | {outcome['pips']:+.1f} pips")
    except Exception as e:
        logger.error(f"Outcome tracker error: {e}")


# ============================================================
# SCHEDULER + APP
# ============================================================
scheduler = AsyncIOScheduler()

telegram_app: Application | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app

    # Load DNA from DB
    for pair in GOLD_PAIRS:
        await _dna[pair].load()
        logger.info(f"🧬 DNA loaded for {pair}: {_dna[pair].snapshot()}")

    # ── Scheduler jobs ───────────────────────────────────────────────────────
    scheduler.add_job(run_gold_signals,            "interval", hours=4,      id="gold_signals")
    scheduler.add_job(check_breakeven,             "interval", minutes=5,    id="breakeven_monitor")
    scheduler.add_job(track_signal_outcomes,       "interval", seconds=60,   id="outcome_tracker")
    scheduler.add_job(update_trailing_stops,       "interval", minutes=5,    id="trailing_stops")
    scheduler.add_job(_risk_commander.check,       "interval", seconds=30,   id="kill_switch_monitor")
    scheduler.add_job(update_dna_weekly,           "cron",     day_of_week="mon", hour=0, minute=5, id="dna_updater")
    scheduler.add_job(friday_volatility_snapshot,  "cron",     day_of_week="fri", hour=21, minute=0, id="friday_snapshot")
    scheduler.add_job(monitor_correlation_matrix,  "interval", hours=4,      id="correlation_monitor")
    scheduler.add_job(send_daily_intelligence_report, "cron",  hour=7, minute=0, id="daily_report")

    scheduler.start()
    logger.info(f"🥇 Gold Signals A+ Elite Edition started — {list(GOLD_PAIRS.keys())}")

    # ── Telegram bot (admin commands) ────────────────────────────────────────
    if TELEGRAM_BOT_TOKEN:
        try:
            telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            telegram_app.add_handler(CommandHandler("gate",   cmd_gate))
            telegram_app.add_handler(CommandHandler("dna",    cmd_dna))
            telegram_app.add_handler(CommandHandler("kill",   cmd_kill))
            telegram_app.add_handler(CommandHandler("status", cmd_status))
            telegram_app.add_handler(CommandHandler("report", cmd_report))
            await telegram_app.initialize()
            await telegram_app.start()
            logger.info("🤖 Telegram admin bot started")
        except Exception as e:
            logger.warning(f"Telegram admin bot failed to start: {e}")

    # ── Initial signal run ───────────────────────────────────────────────────
    asyncio.create_task(run_gold_signals())

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    scheduler.shutdown()
    if telegram_app:
        try:
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception:
            pass
    client.close()


app = FastAPI(title="Grandcom Gold Signals — A+ Elite Edition", lifespan=lifespan)


# ============================================================
# API ENDPOINTS
# ============================================================
@app.get("/api/health")
async def health():
    return {
        "status":       "ok",
        "service":      "gold_signals_elite",
        "version":      "A+",
        "pairs":        list(GOLD_PAIRS.keys()),
        "kill_switch":  is_kill_switch_active(),
        "kill_reason":  _kill_switch_reason,
        "gates_active": sum(1 for v in GATES.values() if v),
        "gates_total":  len(GATES),
    }


@app.get("/api/gold/signals")
async def get_gold_signals(status: str = None, limit: int = 50):
    query = {}
    if status:
        query["status"] = status.upper()
    signals = await db.gold_signals.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"signals": signals, "count": len(signals)}


@app.get("/api/gold/stats")
async def get_gold_stats():
    pipeline = [
        {"$match": {"result": {"$exists": True}}},
        {"$group": {
            "_id":      "$pair",
            "total":    {"$sum": 1},
            "wins":     {"$sum": {"$cond": [{"$eq": ["$result", "WIN"]}, 1, 0]}},
            "losses":   {"$sum": {"$cond": [{"$eq": ["$result", "LOSS"]}, 1, 0]}},
            "net_pips": {"$sum": {"$ifNull": ["$pips", 0]}},
        }},
    ]
    stats = await db.gold_signals.aggregate(pipeline).to_list(10)
    return {"stats": stats}


@app.get("/api/gold/dna")
async def get_dna():
    return {"dna": {pair: _dna[pair].snapshot() for pair in GOLD_PAIRS}}


@app.get("/api/gold/gates")
async def get_gates():
    return {"gates": GATES}


class GateToggleRequest(BaseModel):
    gate: str
    enabled: bool


@app.post("/api/gold/gates/toggle")
async def toggle_gate(req: GateToggleRequest):
    if req.gate not in GATES:
        raise HTTPException(status_code=404, detail=f"Gate '{req.gate}' not found")
    GATES[req.gate] = req.enabled
    return {"gate": req.gate, "enabled": GATES[req.gate]}


@app.get("/api/gold/kelly")
async def get_kelly():
    result = {}
    for pair in GOLD_PAIRS:
        result[pair] = await calculate_kelly_fraction(pair)
    return {"kelly": result}


@app.post("/api/gold/kill")
async def api_kill_switch(reason: str = "API trigger"):
    await activate_kill_switch(reason)
    return {"kill_switch": True, "reason": reason}


@app.post("/api/gold/kill/reset")
async def api_kill_reset():
    await deactivate_kill_switch()
    return {"kill_switch": False}


@app.get("/api/gold/report")
async def api_daily_report():
    await send_daily_intelligence_report()
    return {"status": "report_sent"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

# Grandcom Gold Signals 🥇

**Standalone Gold Trading Service — XAUUSD & XAUEUR**

Sends institutional-grade signals to the `@grandcomgold` Telegram channel.  
Deployed independently on Railway — zero cross-contamination with the Forex service.

---

## Architecture

```
grandcom-gold-signals/
├── backend/
│   ├── gold_server.py          ← Main FastAPI app (entry point)
│   ├── requirements.txt        ← Python dependencies
│   ├── notification_service.py ← Expo push notifications
│   ├── signal_outcome_tracker.py ← Auto TP/SL tracking
│   ├── subscription_service.py ← Stripe subscription management
│   └── ml_engine/              ← ML regime detection engine
│       ├── __init__.py
│       ├── feature_engineering.py
│       ├── regime_detector.py
│       ├── risk_manager.py
│       ├── signal_optimizer.py
│       ├── multi_timeframe.py
│       ├── data_collector.py
│       ├── smart_money.py
│       └── signal_filter.py
├── Procfile                    ← Railway start command
├── nixpacks.toml               ← Railway build config
└── RAILWAY_DEPLOYMENT.md       ← Deployment guide
```

---

## Signal Engine (v2)

The gold signal engine uses a multi-layer analysis pipeline:

| Layer | Component | Weight |
|-------|-----------|--------|
| Trend | MA50 position + ADX(14) | 40% |
| Momentum | MACD cross + RSI + CCI(14) | 30% |
| Triggers | Stochastic(9,6) + Williams%R(14) + StochRSI(14) | 30% |

### Additional Filters
- **H4 Multi-Timeframe** — blocks signals against the higher-timeframe trend
- **DXY Correlation** — blocks BUY signals when USD is in a strong uptrend
- **News Guard** — blocks signals ±60 min around high-impact news events
- **Safety Switch** — prevents chasing overbought/oversold extremes
- **Alignment Score** — Williams%R + StochRSI team vote (boosts confidence by up to 20pts)
- **Candlestick Patterns** — Engulfing, Pin Bar, Doji detection

### Conviction Levels
| Score | Level | Action |
|-------|-------|--------|
| < 60 | LOW | Skip |
| 60–84 | MEDIUM | Send signal |
| ≥ 85 | HIGH CONVICTION 🔥 | Send signal with tag |

---

## Environment Variables

Set these in Railway → Service → Variables:

```
MONGO_URL=<MongoDB connection string>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=<your TwelveData key>
OPENAI_API_KEY=<your OpenAI key>
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/gold/signals` | List signals (optional `?status=ACTIVE&limit=50`) |

---

## Railway Deployment

See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) for full step-by-step instructions.

**Quick start:**
1. Connect this repo to Railway
2. Set Root Directory: `backend/`
3. Add environment variables
4. Deploy

---

## Pairs Covered

| Pair | Symbol | Pip Value |
|------|--------|-----------|
| Gold/USD | XAUUSD | 0.10 |
| Gold/EUR | XAUEUR | 0.10 |

---

## Signal Format (Telegram)

```
🟢 #XAUUSD [SWING] [HIGH CONVICTION 🔥]

Buy 2345.50 - 2346.50

TP1: 2347.20
TP2: 2348.10
TP3: 2349.00

SL: 2342.80
```

Followed by an analysis message with R:R, confidence, H4 trend, DXY status, and news guard status.

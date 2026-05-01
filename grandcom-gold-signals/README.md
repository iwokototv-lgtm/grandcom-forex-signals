# Grandcom Gold Signals — Elite Edition

Standalone Gold trading signals service for **XAUUSD & XAUEUR**.

Sends institutional-grade signals to the **@grandcomgold** Telegram channel.

## Features

- **Pairs**: XAUUSD & XAUEUR
- **Timeframe**: 4H swing strategy
- **AI Engine**: GPT-4o-mini via litellm / emergentintegrations fallback
- **Indicators**: ADX(14) + MACD + MA50 + RSI + CCI + Williams%R + StochRSI + ATR
- **Safety Gates**: News Guard | DXY Correlation | H4 MTF | Circuit Breaker | Session Filter | Entropy | Hurst | Keltner | OBV Divergence | Liquidity Sweep | FVG | Gold-Silver Ratio | VW-MACD | Trailing Stop | Black Box Log
- **Conviction Levels**: Standard (≥70%) and HIGH CONVICTION (≥85%)
- **Breakeven Monitor**: Auto-moves SL to entry when TP1 is hit
- **Trailing Stop**: ATR-based (2.5×ATR) trailing stop after breakeven

## Architecture

```
grandcom-gold-signals/
├── backend/
│   ├── gold_server.py          # Main FastAPI server (Gold signals)
│   ├── notification_service.py # Expo push notifications
│   ├── signal_outcome_tracker.py # Auto-close signals at TP/SL
│   ├── subscription_service.py # Subscription management
│   ├── seed_demo_signals.py    # Demo data seeder
│   ├── send_test_signal.py     # Test utilities
│   ├── update_current_prices.py # Price update script
│   ├── update_mt5_prices.py    # MT5 broker price script
│   ├── test_telegram.py        # Telegram testing
│   ├── nixpacks.toml           # Railway build config
│   ├── requirements.txt        # Python dependencies
│   └── ml_engine/              # ML analysis engine
│       ├── __init__.py
│       ├── data_collector.py
│       ├── feature_engineering.py
│       ├── model_trainer.py
│       ├── multi_timeframe.py
│       ├── regime_detector.py
│       ├── risk_manager.py
│       ├── signal_filter.py
│       ├── signal_optimizer.py
│       └── smart_money.py
├── Procfile
├── requirements.txt
├── .env.example
├── .gitignore
└── RAILWAY_DEPLOYMENT.md
```

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/iwokototv-lgtm/grandcom-gold-signals.git
cd grandcom-gold-signals
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run Locally

```bash
cd backend
uvicorn gold_server:app --host 0.0.0.0 --port 8001 --reload
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/gold/signals` | List signals (optional `?status=ACTIVE`) |
| `GET /api/gold/stats` | Win rate, active count, throttle state |
| `GET /api/gold/breakeven` | Signals with breakeven triggered |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MONGO_URL` | MongoDB connection string |
| `DB_NAME` | Database name (default: `gold_signals`) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_GOLD_CHANNEL_ID` | Channel ID (default: `@grandcomgold`) |
| `TWELVE_DATA_API_KEY` | TwelveData API key for price data |
| `OPENAI_API_KEY` | OpenAI API key for AI analysis |

## Railway Deployment

See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) for full Railway setup guide.

**Root Directory**: `backend/`  
**Start Command**: `uvicorn gold_server:app --host 0.0.0.0 --port ${PORT:-8001}`

## Signal Format (Telegram)

```
🟢 XAUUSD BUY

Buy 2345.50 - 2346.50

TP1: 2360.00
TP2: 2375.00
TP3: 2390.00

SL: 2330.00

────────────────────────────
📈 UPTREND | SWING
R:R: 1:2.5 | Conf: 82% | Score: 78/100
⏰ 2025-01-15 14:30 UTC
Grandcom Gold EA
```

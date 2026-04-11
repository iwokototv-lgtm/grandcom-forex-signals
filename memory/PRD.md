# Grandcom Forex Signals Pro - Product Requirements Document

## Overview
Professional Forex & Gold (XAUUSD/XAUEUR) signals system with fully automatic signal generation, AI analysis, and Telegram delivery.

## Architecture
- **Forex Backend** (`backend/server.py`) — FastAPI, handles all forex pairs, sends to `@grandcomsignals`
- **Gold Backend** (`gold/gold_server.py`) — Standalone FastAPI, handles XAUUSD + XAUEUR only, sends to `@grandcomgold`
- **Frontend**: React Native / Expo
- **Database**: MongoDB
- **ML**: scikit-learn, hmmlearn
- **AI**: GPT-4o-mini via Emergent LLM (emergentintegrations)

## Telegram Channels
- `@grandcomsignals` — Forex signals (EURUSD, GBPUSD, USDJPY, etc.)
- `@grandcomgold` — Gold signals (XAUUSD, XAUEUR)
- Bot: `@GrandcomBot` (ID: 8526275676)

## Gold Server Config
- Pairs: XAUUSD, XAUEUR
- Strategy: ATR-based swing (4H timeframe)
- ATR multipliers: SL=1.5x, TP1=2.0x, TP2=3.5x, TP3=5.0x
- Min confidence: 60%
- Signal interval: 2 minutes
- No gatekeeper (ATR handles risk management)
- **Bidirectional signal scoring**: Technical indicator scoring (RSI zones, MACD crossovers, BB position, MA alignment) determines BUY/SELL direction. Strong tech scores override AI if it disagrees.

## Fixes Applied (April 2026)
1. AI analysis JSON parsing — markdown fence stripping + fallback extraction
2. Confidence threshold restored from 70% to 60%
3. MTF confirmation skipped for gold pairs
4. RANGE regime allowed for gold pairs
5. EMA50 proximity check skipped for gold
6. Gatekeeper skipped for gold (ATR swing handles risk)
7. `strategy` variable bug fixed (was `active_strategy`)
8. Gold SL max increased from 50 to 100
9. Gold R:R min lowered from 1.8 to 1.0
10. Separated gold into standalone server (`gold/gold_server.py`)
11. Gold channel routing to `@grandcomgold`
12. Stale ACTIVE signals cleaned from DB
13. **Fixed BUY-only bias** — Added technical indicator scoring system (RSI zones, MACD crossovers, BB position, MA alignment) with balanced AI prompt. SELL signals now generated when bearish conditions exist. Strong tech scores (abs>=3) override AI direction.
14. **Applied same balanced AI scoring to Forex server** — Both forex and gold servers now use identical technical scoring + balanced AI prompt. Regime enforcement preserved: TREND_UP=BUY only, TREND_DOWN=SELL only.

## Deployment
- **Emergent Preview**: Both servers running (forex on 8001, gold on 8002)
- **Railway (Forex)**: `backend/` folder, `main` branch
- **Railway (Gold)**: `gold/` folder, `main` branch — awaiting token reset (3 days)

## GitHub
- Repo: `iwokototv-lgtm/grandcom-forex-signals`
- Branch `main`: Contains both `backend/` and `gold/` folders
- Branch `Grandcomgold`: Feature branch (merged into main)

## Railway Environment Variables (Gold Service)
```
MONGO_URL=<from forex service>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=8526275676:AAGC5oSN0KDiXmwiUWrL5RxzGv2-2umCmqA
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=7a74d13b2bb448d68f5c348245ae994b
OPENAI_API_KEY=sk-emergent-cA500137aA67f7cC2F
```

## Backlog
- P0: Deploy gold service on Railway (when tokens reset)
- P1: TSCopier format optimization for gold signals
- P1: Fix forex gatekeeper max_open_trades (currently blocking at 17/3)
- P2: Admin Dashboard
- P2: Payment system completion (Stripe)
- P3: Email notifications
- P3: Enhanced analytics

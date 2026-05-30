# Grandcom Gold Signals — Institutional Multi-Strategy Hybrid Portfolio System v3.0

[![Railway Deploy](https://railway.app/button.svg)](https://railway.app)

## Overview

Production-grade institutional trading signal system for XAUUSD and XAUEUR, powered by a 16-module ML engine with 6 confirmed institutional-grade components.

## System Components (6/6 Confirmed)

| Component | Description | Status |
|-----------|-------------|--------|
| **G1: Daily Pivot Points** | 4 methods (Standard, Fibonacci, Woodie, Camarilla), 6 levels, 6 zones | ✅ Active |
| **G2: Multi-Timeframe Confirmation** | 1H, 4H, Daily, Weekly alignment (0-100% score) | ✅ Active |
| **G3: Regime Detection** | 5 market regimes with adaptive parameters | ✅ Active |
| **SMC/Institutional Structure** | Order Blocks, Liquidity Voids, FVGs, BOS/ChoCH | ✅ Active |
| **Correlation/Exposure Engine** | Rolling windows, Beta exposure, USD clustering | ✅ Active |
| **Multi-Timeframe Consensus** | Cross-timeframe validation and signal routing | ✅ Active |

## Architecture

```
gold_server_v3.py          ← FastAPI application (11 endpoints)
config.py                  ← Centralized configuration (50+ variables)
ml_engine/
├── hybrid_portfolio_system_v3.py   ← System integration
├── regime_detector.py              ← G3: Market regime (ADX, BB, RSI, MA Slope)
├── smc_ict_strategy.py             ← SMC/ICT: OBs, FVGs, Liquidity Voids
├── mean_reversion_strategy.py      ← Mean reversion (Z-score, BB, RSI)
├── multi_timeframe_confirmation.py ← G2: 1H/4H/D/W alignment
├── pivot_points_analyzer.py        ← G1: Daily pivots (4 methods)
├── correlation_engine.py           ← Rolling + Beta + USD clustering
├── risk_parity.py                  ← Equal risk contribution allocation
├── volatility_adjustment.py        ← Dynamic position sizing
├── drawdown_recovery.py            ← Gradual recovery management
├── economic_calendar.py            ← High-impact event filtering
├── performance_attribution.py      ← P&L attribution by strategy/regime
├── trade_journal.py                ← Trade logging and pattern analysis
├── position_calculator.py          ← Multi-method position sizing
├── portfolio_manager.py            ← Portfolio state and risk oversight
├── strategy_router.py              ← Signal routing by regime
├── feature_engineering.py          ← ML feature extraction
├── risk_manager.py                 ← Core risk management
├── signal_filter.py                ← Signal quality filtering
└── smart_money.py                  ← Legacy SMC analysis
```

## API Endpoints (11)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Railway health check |
| GET | `/api/signals` | Get stored signals |
| GET | `/api/system/status` | Full system status |
| GET | `/api/analysis/regime/{pair}` | Market regime analysis |
| GET | `/api/analysis/smc/{pair}` | SMC/ICT analysis |
| GET | `/api/analysis/pivots/{pair}` | Pivot points (all 4 methods) |
| GET | `/api/analysis/mtf/{pair}` | Multi-timeframe confirmation |
| GET | `/api/analysis/hybrid/{pair}` | Full hybrid analysis |
| GET | `/api/portfolio/state` | Portfolio state |
| GET | `/api/performance` | Performance attribution |
| POST | `/api/signals/trigger` | Manual signal trigger |

## Quick Start

### Railway Deployment
```bash
# Deploy via Railway CLI
railway up

# Or connect GitHub repo to Railway dashboard
# Railway will auto-detect Dockerfile and deploy
```

### Local Development
```bash
# Clone and setup
cp .env.example .env
# Fill in your API keys in .env

# Docker Compose
docker-compose up -d

# Or direct Python
cd backend
pip install -r requirements.txt
uvicorn gold_server_v3:app --host 0.0.0.0 --port 8002 --reload
```

## Environment Variables

See `.env.example` for the complete list of 50+ configuration variables.

**Required:**
- `MONGO_URL` — MongoDB connection string
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_GOLD_CHANNEL_ID` — Target channel ID
- `TWELVE_DATA_API_KEY` — TwelveData API key
- `OPENAI_API_KEY` — OpenAI API key (for GPT-4o-mini)

## Key Statistics

- **Total Modules:** 16
- **Lines of Code:** 10,000+
- **API Endpoints:** 11
- **Configuration Variables:** 50+
- **Timeframes:** 4 (1H, 4H, Daily, Weekly)
- **Market Regimes:** 5
- **Trading Strategies:** 3
- **Correlation Engines:** 3
- **Pivot Point Methods:** 4

## Runtime

- **Python:** 3.11
- **Framework:** FastAPI + Uvicorn
- **Database:** MongoDB (Motor async driver)
- **Scheduler:** APScheduler
- **ML:** scikit-learn, scipy, numpy, pandas
- **Deployment:** Railway (Docker)

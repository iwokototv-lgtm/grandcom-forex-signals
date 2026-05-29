# Grandcom Gold Signals v3.0

**Institutional Multi-Strategy Hybrid Portfolio System**
XAUUSD & XAUEUR — Railway Production Ready

---

## Overview

Grandcom Gold Signals v3.0 is a complete institutional-grade trading signal system for gold (XAUUSD and XAUEUR). It combines multi-timeframe analysis, regime-adaptive strategy routing, advanced correlation management, and dynamic risk sizing into a single unified pipeline.

## Architecture

```
Signal Generation Pipeline
──────────────────────────
1. Economic Calendar Check     → Block trading around high-impact news
2. Multi-Timeframe Analysis    → 1H + 4H + Daily + Weekly confluence
3. Feature Extraction          → 30+ technical features
4. Regime Detection            → 5 market regimes (ML + rule-based)
5. Strategy Routing            → SMC/ICT or Mean Reversion
6. Portfolio Approval          → Correlation + drawdown + exposure checks
7. Position Sizing             → Risk parity + vol adjustment + DD recovery
8. Signal Delivery             → MongoDB storage + Telegram broadcast
```

## System Components

### Core Server
- `backend/gold_server_v3.py` — FastAPI application, 11 API endpoints
- `backend/config.py` — Centralised configuration (50+ settings)

### ML Engine Modules (15 modules)

| Module | Purpose |
|--------|---------|
| `regime_detector.py` | Market condition identification (5 regimes) |
| `feature_engineering.py` | 30+ technical feature extraction |
| `multi_timeframe.py` | 1H, 4H, Daily, Weekly alignment |
| `smc_ict_strategy.py` | Smart Money Concepts / ICT methodology |
| `mean_reversion_strategy.py` | Overbought/oversold trading |
| `correlation_engine.py` | Rolling correlation, Beta, USD clustering |
| `risk_parity_allocator.py` | Equal risk contribution allocation |
| `volatility_adjuster.py` | Dynamic position sizing by vol regime |
| `drawdown_recovery.py` | Drawdown monitoring and recovery |
| `economic_calendar.py` | News event blackout windows |
| `performance_attributor.py` | P&L attribution by strategy/regime/pair |
| `trade_journal.py` | Full trade lifecycle tracking |
| `position_calculator.py` | Unified position sizing pipeline |
| `portfolio_manager.py` | Multi-strategy portfolio orchestration |
| `strategy_router.py` | Regime-based strategy selection |
| `hybrid_portfolio_v2.py` | Complete system integration |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Railway health check |
| `/api/signals` | GET | Stored signals (filterable) |
| `/api/portfolio` | GET | Portfolio state |
| `/api/portfolio/status` | GET | Full system status |
| `/api/regime` | GET | Current market regime |
| `/api/mtf` | GET | Multi-timeframe analysis |
| `/api/correlation` | GET | Correlation matrix |
| `/api/performance` | GET | Performance attribution |
| `/api/journal` | GET | Trade journal |
| `/api/calendar` | GET | Economic calendar |
| `/api/drawdown` | GET | Drawdown status |
| `/api/signals/generate` | POST | Manual signal trigger |

## Market Regimes

| Regime | Active Strategies | Risk Multiplier |
|--------|------------------|-----------------|
| TREND_UP | SMC/ICT, Trend Following | 1.0× |
| TREND_DOWN | SMC/ICT, Trend Following | 1.0× |
| RANGE | Mean Reversion | 0.8× |
| HIGH_VOL | SMC/ICT (breakout only) | 0.6× |
| LOW_VOL | Mean Reversion | 1.2× |
| CHAOS | None (trading suspended) | 0.0× |

## Quick Start

### Local Development

```bash
cp .env.example .env
# Fill in your API keys in .env
docker-compose up
```

### Railway Deployment

1. Push this repository to GitHub
2. Create a new Railway project
3. Connect the GitHub repository
4. Set environment variables (see `.env.example`)
5. Railway auto-deploys on push

## Environment Variables

See `.env.example` for the complete list of 50+ configuration options.

**Required:**
- `MONGO_URL` — MongoDB connection string
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_GOLD_CHANNEL_ID` — Target channel ID
- `TWELVE_DATA_API_KEY` — TwelveData market data API key
- `OPENAI_API_KEY` — OpenAI API key (for GPT analysis)

## Version History

- **v3.0.0** — Hybrid Portfolio System: 15 ML modules, 5 risk components, 3 strategies
- **v2.0.0** — ML regime detection, SMC analysis, signal quality filtering
- **v1.0.0** — Initial gold signal server (GPT-4o-mini + TwelveData)

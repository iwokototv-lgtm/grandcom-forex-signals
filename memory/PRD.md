# Grandcom Forex Signals Pro - Product Requirements Document

## Original Problem Statement
Build a professional Forex & Gold (XAUUSD) signals mobile app named "Grandcom Forex Signals Pro". The core feature is a fully automatic signal generation system that posts trading signals to a Telegram channel with automatic profit-taking and trade closure.

## User Personas
- **Primary User**: Forex traders who want automated trading signals
- **Admin**: System administrator managing signal generation and monitoring

## Core Requirements

### Signal Generation (COMPLETED)
- Fully automatic signal generation using live market data
- Support for Forex pairs, Gold (XAUUSD, XAUEUR), and crypto (BTCUSD)
- Each signal includes: Entry Price, Stop Loss, TP1, TP2, TP3
- ML-powered market regime detection (Trend, Range, Volatile)
- Smart Money Concepts (Order Blocks, Fair Value Gaps)
- Multi-Timeframe Analysis (H4/H1/M15)
- Advanced filters (News, Session, Correlation)

### Automatic Signal Outcome Tracking (COMPLETED - December 2025)
- Background job monitors active signals every 60 seconds
- Automatically detects when TP/SL levels are hit
- Updates signal status: CLOSED_TP1/TP2/TP3 or CLOSED_SL
- Records pips gained/lost
- Sends "Trade Closed" notifications to Telegram
- Rate limiting to avoid Telegram flood control

### Telegram Integration (COMPLETED)
- Automatically posts signals to @grandcomsignals channel
- Professional format with entry, TP levels, SL, regime info
- Trade closed notifications

### Authentication (COMPLETED)
- JWT-based email/password login
- Pre-defined admin account: admin@forexsignals.com

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main FastAPI app, scheduler, APIs
│   ├── signal_outcome_tracker.py    # Auto TP/SL monitoring (NEW)
│   ├── ml_engine/
│   │   ├── regime_detector.py       # ML market regime classification
│   │   ├── signal_filter.py         # Quality filtering & TP/SL calc
│   │   ├── multi_timeframe.py       # MTF analysis
│   │   ├── smc_analysis.py          # Smart Money Concepts
│   │   └── ...
│   └── .env
├── frontend/                        # React Native (Expo) app
│   ├── app/(tabs)/
│   │   ├── home.tsx                 # Signal list with live ticker
│   │   ├── analytics.tsx            # ML & MTF dashboard
│   │   └── ...
│   └── .env
└── desktop/                         # Electron wrapper for Windows
```

## Key API Endpoints

### Authentication
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login
- `GET /api/auth/me` - Get current user

### Signals
- `GET /api/signals` - Get signals list
- `GET /api/signals/active` - Get active signals being tracked
- `GET /api/signals/tracker-status` - Get outcome tracker status
- `POST /api/signals/check-outcomes` - Manual trigger outcome check
- `GET /api/signals/history` - Signal history with win/loss stats

### ML Analytics
- `GET /api/ml/regime/{symbol}` - Market regime for symbol
- `GET /api/ml/mtf/{symbol}` - Multi-timeframe analysis
- `GET /api/ml/smc/{symbol}` - Smart Money Concepts
- `GET /api/stats` - Overall performance statistics

## Database Schema

### signals collection
```javascript
{
  _id: ObjectId,
  pair: String,           // e.g., "XAUUSD"
  type: String,           // "BUY" or "SELL"
  entry_price: Number,
  tp_levels: [Number],    // [TP1, TP2, TP3]
  sl_price: Number,
  status: String,         // ACTIVE, CLOSED_TP1/2/3, CLOSED_SL
  result: String,         // WIN, LOSS
  pips: Number,
  exit_price: Number,
  created_at: Date,
  closed_at: Date,
  regime: String,         // TREND, RANGE, VOLATILE
  confidence: Number
}
```

## Third-Party Integrations
- **OpenAI GPT-5.2**: AI analysis (via Emergent LLM Key)
- **Twelve Data API**: Live market data (Grow plan)
- **Telegram Bot API**: Signal posting to channel

## What's Implemented (December 2025)

### Session Accomplishments
1. **Signal Outcome Tracker** - NEW CRITICAL FEATURE
   - Created `/app/backend/signal_outcome_tracker.py`
   - Background task checks active signals every 60 seconds
   - Automatically closes signals when TP/SL hit
   - Sends Telegram notifications with rate limiting
   - Integrated into server.py startup

2. **API Endpoints for Tracking**
   - `GET /api/signals/tracker-status` - Monitor tracker status
   - `GET /api/signals/active` - View active signals
   - `POST /api/signals/check-outcomes` - Manual trigger

3. **Statistics Update**
   - Updated `/api/stats` to count new CLOSED_TP1/2/3 statuses
   - Now shows accurate win rate, pips, wins/losses

### Current Performance
- Win Rate: ~52%
- Average Pips: ~139
- Tracker running 24/7 checking signals every minute

## Prioritized Backlog

### P0 - Critical (Done)
- [x] Automatic signal outcome tracking
- [x] TP/SL monitoring every 60 seconds
- [x] Trade closed Telegram notifications

### P1 - High Priority
- [ ] Push Notifications via Expo
- [ ] Historical backtesting engine

### P2 - Medium Priority
- [ ] In-App Purchases / Subscription
- [ ] Admin Management UI

### P3 - Future
- [ ] More granular TP/SL tuning per market condition
- [ ] Advanced reporting dashboard

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram Channel**: @grandcomsignals

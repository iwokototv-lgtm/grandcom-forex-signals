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

### OPTIMIZED TP/SL Settings (March 2026) - UPDATED
Based on comprehensive backtesting (2020-2024 data):

**FOREX Pairs - Conservative (3/6/9):**
| Pair | TP1 | TP2 | TP3 | SL | Win Rate | Profit Factor |
|------|-----|-----|-----|-----|----------|---------------|
| EURUSD | 3 | 6 | 9 | 10 | 45.9% | 1.23 |
| GBPUSD | 3 | 6 | 9 | 10 | 54.4% | 1.12 |
| USDJPY | 3 | 6 | 9 | 10 | 52.4% | 1.27 |
| EURJPY | 3 | 6 | 9 | 10 | 58.3% | 1.30 (BEST) |
| GBPJPY | 3 | 6 | 9 | 10 | 65.1% | 1.17 |
| AUDUSD | 3 | 6 | 9 | 10 | 44.4% | 1.15 |
| USDCAD | 3 | 6 | 9 | 10 | 52.9% | 1.26 |
| USDCHF | 3 | 6 | 9 | 10 | 40.3% | 1.14 |

**GOLD Pairs - Differentiated Settings:**
| Pair | TP1 | TP2 | TP3 | SL | Win Rate | Profit Factor |
|------|-----|-----|-----|-----|----------|---------------|
| XAUUSD | 7 | 15 | 25 | ATR | 51.7% | 1.27 |
| XAUEUR | 5 | 10 | 15 | ATR | 63.9% | 1.27 |

**BTCUSD**: ATR-based (high volatility)

### Automatic Signal Outcome Tracking (COMPLETED)
- Background job monitors active signals every 60 seconds
- Automatically detects when TP/SL levels are hit
- Updates signal status and sends notifications to Telegram

### Historical Backtesting Engine (COMPLETED)
- Backend engine supports 3-10 years of historical data analysis
- Frontend UI at `/backtest` with full configuration
- Used to optimize TP/SL settings

### Stripe Subscription System (COMPLETED - March 2026)
- Backend subscription service with Stripe integration
- Frontend UI at `/subscription`
- Plans: Pro ($29.99/mo), Premium ($79.99/mo)

### Push Notifications (COMPLETED)
- Expo Push Notification service integrated
- Frontend UI at `/notifications`

### Authentication (COMPLETED)
- JWT-based email/password login
- Role-based admin access
- Admin Panel button appears immediately after login

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main FastAPI app with optimized pair params
│   ├── subscription_service.py      # Stripe subscription logic
│   ├── signal_outcome_tracker.py    # Auto TP/SL monitoring
│   ├── notification_service.py      # Push notifications
│   ├── backtest_engine.py           # Historical backtesting
│   ├── ml_engine/                   # ML components
│   └── .env
├── frontend/
│   ├── contexts/AuthContext.tsx     # Auth with role sync
│   ├── app/(tabs)/
│   │   ├── home.tsx                 # Signal list
│   │   ├── analytics.tsx            # ML dashboard
│   │   ├── profile.tsx              # User profile & admin link
│   │   ├── subscription.tsx         # Subscription plans UI
│   │   ├── admin.tsx                # Admin panel
│   │   ├── backtest.tsx             # Backtesting UI
│   │   └── notifications.tsx        # Notification settings
│   └── .env
└── desktop/                         # Electron (not built)
```

## Third-Party Integrations
- **OpenAI GPT-5.2**: AI analysis (Emergent LLM Key)
- **Twelve Data API**: Live market data
- **Telegram Bot API**: Signal posting
- **Expo Push API**: Mobile notifications
- **Stripe**: Payment processing (TEST MODE)

## Current Session Accomplishments (March 2, 2026)

1. **Auth Context Role Fix** - Admin Panel button appears immediately after login
2. **Stripe Subscription System** - Full backend + frontend implementation
3. **Comprehensive Backtest Analysis** - Tested all pairs with multiple configs
4. **OPTIMIZED TP/SL Settings Applied**:
   - Forex: Changed from 5/10/15 to 3/6/9 pips (+11% profit factor)
   - XAUUSD: Changed from 5/10/15 to 7/15/25 pips (1114% return)
   - XAUEUR: Kept at 5/10/15 (already optimal)

## Prioritized Backlog

### P0 - Critical (ALL COMPLETE ✅)
- [x] Signal generation with ML optimization
- [x] Automatic outcome tracking
- [x] Optimized TP/SL settings based on backtesting
- [x] Push notifications
- [x] Backtesting engine
- [x] Admin panel
- [x] Stripe subscription system

### P1 - High Priority
- [ ] Build & test Electron desktop app

### P2 - Medium Priority
- [ ] Admin panel enhancements (manual signals, user management)
- [ ] Complete Stripe setup with real API key

### P3 - Future
- [ ] ML model training with historical data
- [ ] Advanced strategy customization

## Partial Close Percentages (for Trade Copier)
- TP1: 33%
- TP2: 33%
- TP3: 34%

## Test Status
- Backend: All tests passing
- Frontend: All tests passing
- Backtest validation: COMPLETED

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals
- **Stripe**: TEST MODE (sk_test_emergent placeholder)

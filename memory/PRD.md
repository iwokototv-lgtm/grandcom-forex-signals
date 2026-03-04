# Grandcom Forex Signals Pro - Product Requirements Document

## Overview
Professional Forex & Gold (XAUUSD) signals mobile app with fully automatic signal generation system that posts trading signals to a Telegram channel.

## Active Trading Pairs (13 pairs) - Updated March 4, 2025

| Pair | TP1 | TP2 | TP3 | SL | Status | Sessions |
|------|-----|-----|-----|-----|--------|----------|
| XAUUSD | 7 | 15 | 25 | ATR | RE-ENABLED | London + NY |
| XAUEUR | 5 | 10 | 15 | ATR | RE-ENABLED | London |
| EURUSD | 3 | 6 | 9 | 10 | ACTIVE | London |
| GBPUSD | 3 | 6 | 9 | 10 | ACTIVE | London |
| USDJPY | 3 | 6 | 9 | 10 | ACTIVE | Asian + NY |
| EURJPY | 3 | 6 | 9 | 10 | ACTIVE | ALL (22h) |
| GBPJPY | 3 | 6 | 9 | 10 | RE-ENABLED | Asian + London + NY |
| AUDUSD | 2 | 4 | 6 | 8 | RE-ENABLED | Asian + NY |
| USDCAD | 3 | 6 | 9 | 10 | ACTIVE | NY |
| USDCHF | 3 | 6 | 9 | 10 | ACTIVE | London |
| NZDUSD | 3 | 6 | 9 | 10 | ACTIVE | Asian + NY |
| AUDJPY | 3 | 6 | 9 | 10 | ACTIVE | Asian + NY |
| CADJPY | 3 | 6 | 9 | 10 | ACTIVE | Asian + NY |

## Disabled Pairs (1 pair)

| Pair | Reason |
|------|--------|
| BTCUSD | 17.5% win rate, PF 0.14 - too volatile |

## Session Schedule

### Asian Session (0:00-8:00 UTC) - 6 pairs
- USDJPY, EURJPY, GBPJPY, NZDUSD, AUDJPY, CADJPY

### London Session (8:00-16:00 UTC) - 5 pairs
- EURUSD, USDCHF, GBPUSD, EURJPY, XAUUSD, XAUEUR

### New York Session (13:00-21:00 UTC) - 8 pairs
- USDJPY, USDCAD, EURJPY, NZDUSD, AUDJPY, CADJPY, XAUUSD, AUDUSD

## Profitability Filters

| Filter | Setting | Purpose |
|--------|---------|---------|
| Regime Filter | Skip RANGE, VOLATILE | Only trade trends |
| Confidence | Min 55% AI, 60% Regime | Quality signals only |
| Session | Per-pair hours | Optimal liquidity |
| Drawdown | Max 3 losses, 50 pips/day | Risk management |

## Technical Stack
- **Frontend:** React Native, Expo, Expo Router
- **Backend:** FastAPI, Pymongo, APScheduler, python-jose (JWT)
- **Database:** MongoDB
- **Integrations:** Twelve Data API, Telegram Bot API, OpenAI/Emergent LLM, Stripe
- **Desktop:** Electron

## Desktop App
- Windows Portable: `/app/desktop/dist/Grandcom-Forex-Signals-Pro-Windows-Portable.zip`
- Linux AppImage: `/app/desktop/dist/Grandcom Forex Signals Pro-1.0.0-arm64.AppImage`

## Key API Endpoints

### Authentication
- `POST /api/auth/register` - User registration
- `POST /api/auth/login` - User login

### Signals
- `GET /api/signals` - Get all signals
- `GET /api/signals/active` - Get active signals
- `GET /api/signals/{id}` - Get specific signal

### Admin
- `GET /api/admin/system-config` - System configuration
- `GET /api/admin/filters` - Filter settings
- `GET /api/admin/filter-stats` - Filter statistics
- `GET /api/admin/ml/performance` - ML performance
- `POST /api/admin/signals/manual` - Create manual signal
- `POST /api/admin/users/{id}` - Update user

### Subscriptions (Stripe)
- `POST /api/subscriptions/create-checkout-session`
- `POST /api/stripe-webhook`

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals

## Completed Tasks
- [x] Re-enabled XAUUSD, XAUEUR, GBPJPY, AUDUSD pairs
- [x] Stripe subscription system
- [x] Auth context bug fix
- [x] Backtesting and live optimization
- [x] Desktop app build (Windows + Linux)
- [x] Admin panel enhancements
- [x] ML performance analysis
- [x] Advanced profitability filters
- [x] Asian session expansion

## Upcoming Tasks (P1-P2)
- [ ] Validate Desktop App functionality
- [ ] Admin Panel UI for filter parameters

## Future Tasks (P3)
- [ ] Economic Calendar Integration
- [ ] Dynamic Volatility-Based TPs (ATR)
- [ ] Improve Backtest Engine accuracy

## Known Items
- Stripe subscription uses TEST key - requires production key for real payments
- Historical backtests show lower performance than live trading (trust live data)

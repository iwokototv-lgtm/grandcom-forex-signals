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

### Fixed Pip TP Levels for Forex (COMPLETED)
- Forex pairs use FIXED pip targets: TP1=5, TP2=10, TP3=15 pips
- XAUUSD, XAUEUR also use fixed 5/10/15 pip targets
- BTCUSD uses ATR-based dynamic TPs

### Automatic Signal Outcome Tracking (COMPLETED)
- Background job monitors active signals every 60 seconds
- Automatically detects when TP/SL levels are hit
- Updates signal status and sends notifications to Telegram

### Historical Backtesting Engine (COMPLETED)
- Backend engine supports 3-10 years of historical data analysis
- Frontend UI at `/backtest` with full configuration

### Push Notifications (COMPLETED)
- Expo Push Notification service integrated
- Frontend UI at `/notifications`

### Telegram Integration (COMPLETED)
- Posts signals to @grandcomsignals channel
- Trade closed notifications

### Authentication (COMPLETED)
- JWT-based email/password login
- Role-based admin access

### Stripe Subscription System (COMPLETED - March 2026)
- Backend subscription service with Stripe integration
- API Endpoints:
  - `GET /api/subscriptions/packages` - Get available plans
  - `GET /api/subscriptions/current` - Get user's subscription status
  - `POST /api/subscriptions/create-checkout-session` - Create Stripe checkout
  - `GET /api/subscriptions/verify/{session_id}` - Verify payment
  - `POST /api/subscriptions/cancel` - Cancel subscription
  - `POST /api/webhook/stripe` - Stripe webhook handler
- Frontend UI at `/subscription` showing:
  - Current plan status
  - Pro Monthly ($29.99), Pro Yearly ($299.99)
  - Premium Monthly ($79.99), Premium Yearly ($799.99)
  - Feature comparisons and Subscribe buttons

### Auth Context Role Fix (COMPLETED - March 2026)
- User role is now refreshed from server on app load
- Admin Panel button appears immediately after admin login

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main FastAPI app
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

## Database Collections

### signals
- pair, type, entry_price, tp_levels[], sl_price
- status, result, pips, created_at, closed_at
- regime, confidence

### subscriptions
- user_id, package_id, tier, status
- starts_at, expires_at, payment_session_id

### payment_transactions
- user_id, session_id, package_id
- amount, currency, status, payment_status

### push_tokens
- user_id, push_token, device_type, is_active

## Third-Party Integrations
- **OpenAI GPT-5.2**: AI analysis (Emergent LLM Key)
- **Twelve Data API**: Live market data
- **Telegram Bot API**: Signal posting
- **Expo Push API**: Mobile notifications
- **Stripe**: Payment processing (TEST MODE - uses placeholder key)

## Current Session Accomplishments (March 2026)

1. **Auth Context Role Fix**
   - Added server-side user data refresh on app load
   - Admin Panel button now appears immediately after login

2. **Stripe Subscription System**
   - Created subscription_service.py with tier management
   - Added 6 subscription endpoints to server.py
   - Built subscription.tsx frontend UI
   - Stripe checkout session creation working

3. **Minor Fixes**
   - Fixed Ionicons warning (logo-telegram → send)

## Prioritized Backlog

### P0 - Critical (ALL COMPLETE ✅)
- [x] Signal generation with ML optimization
- [x] Automatic outcome tracking
- [x] Fixed pip TP levels
- [x] Push notifications
- [x] Backtesting engine
- [x] Admin panel
- [x] Stripe subscription system

### P1 - High Priority
- [ ] Run & analyze backtests for optimal settings
- [ ] Build & test Electron desktop app

### P2 - Medium Priority
- [ ] Admin panel enhancements (manual signals, user management)
- [ ] Complete Stripe setup with real API key

### P3 - Future
- [ ] ML model training with historical data
- [ ] Advanced strategy customization

## Test Status
- Backend: 18/18 tests passing (100%)
- Frontend: 22/22 tests passing (100%)
- Test files: /app/tests/e2e/*.spec.ts, /app/backend/tests/test_*.py

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals
- **Stripe**: TEST MODE (sk_test_emergent placeholder)

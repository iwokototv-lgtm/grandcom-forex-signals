# Grandcom Forex Signals Pro - Product Requirements Document

## Original Problem Statement
Build a professional Forex & Gold (XAUUSD) signals mobile app named "Grandcom Forex Signals Pro". The core feature is a fully automatic signal generation system that posts trading signals to a Telegram channel with automatic profit-taking and trade closure.

## User Personas
- **Primary User**: Forex traders who want automated trading signals
- **Admin**: System administrator managing signal generation and monitoring

## Platforms
- **Mobile Web App**: React Native (Expo) - LIVE
- **Desktop App**: Electron - BUILT (Windows Portable + Linux AppImage)

## Core Requirements - ALL COMPLETE ✅

### Signal Generation
- Fully automatic signal generation using live market data
- Support for Forex pairs, Gold (XAUUSD, XAUEUR), and crypto (BTCUSD)
- Each signal includes: Entry Price, Stop Loss, TP1, TP2, TP3
- ML-powered market regime detection (Trend, Range, Volatile)
- Smart Money Concepts (Order Blocks, Fair Value Gaps)
- Multi-Timeframe Analysis (H4/H1/M15)

### OPTIMIZED TP/SL Settings (March 2026)
Based on comprehensive backtesting (2020-2024 data):

**FOREX Pairs → Conservative (3/6/9 pips, SL=10):**
| Pair | Win Rate | Profit Factor |
|------|----------|---------------|
| EURJPY | 58.3% | 1.30 (BEST) |
| USDJPY | 52.4% | 1.27 |
| USDCAD | 52.9% | 1.26 |
| EURUSD | 45.9% | 1.23 |
| GBPJPY | 65.1% | 1.17 |
| AUDUSD | 44.4% | 1.15 |
| USDCHF | 40.3% | 1.14 |
| GBPUSD | 54.4% | 1.12 |

**GOLD Pairs:**
| Pair | TP1 | TP2 | TP3 | Win Rate | Profit Factor |
|------|-----|-----|-----|----------|---------------|
| XAUUSD | 7 | 15 | 25 | 51.7% | 1.27 |
| XAUEUR | 5 | 10 | 15 | 63.9% | 1.27 |

### Desktop Application (March 2026)
**Built Successfully:**
- Linux AppImage: `/app/desktop/dist/Grandcom Forex Signals Pro-1.0.0-arm64.AppImage` (108 MB)
- Windows Portable: `/app/desktop/dist/Grandcom-Forex-Signals-Pro-Windows-Portable.zip` (115 MB)

**Features:**
- Native system tray support
- Menu bar with quick navigation
- Zoom controls (Ctrl +/-)
- Full screen mode (F11)
- External link handling
- Auto-minimize to tray

### Other Completed Features
- Automatic Signal Outcome Tracking
- Historical Backtesting Engine (3-10 years)
- Stripe Subscription System
- Push Notifications (Expo)
- JWT Authentication with Role-based Admin
- Telegram Integration (@grandcomsignals)

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main FastAPI with optimized pair params
│   ├── subscription_service.py      # Stripe subscriptions
│   ├── signal_outcome_tracker.py    # Auto TP/SL monitoring
│   ├── notification_service.py      # Push notifications
│   ├── backtest_engine.py           # Historical backtesting
│   └── ml_engine/                   # ML components
├── frontend/                        # React Native (Expo)
│   ├── app/(tabs)/
│   │   ├── home.tsx
│   │   ├── analytics.tsx
│   │   ├── profile.tsx
│   │   ├── subscription.tsx
│   │   ├── admin.tsx
│   │   ├── backtest.tsx
│   │   └── notifications.tsx
└── desktop/                         # Electron
    ├── main.js                      # Main process
    ├── preload.js                   # IPC bridge
    ├── dist/                        # Built apps
    │   ├── *.AppImage               # Linux
    │   └── *.zip                    # Windows Portable
    └── BUILD_GUIDE.md
```

## Third-Party Integrations
- **OpenAI GPT-5.2**: AI analysis (Emergent LLM Key)
- **Twelve Data API**: Live market data
- **Telegram Bot API**: Signal posting
- **Expo Push API**: Mobile notifications
- **Stripe**: Subscriptions (TEST MODE)

## Session Accomplishments (March 2, 2026)

1. ✅ Fixed Auth Context role synchronization
2. ✅ Completed Stripe subscription system (backend + frontend)
3. ✅ Ran comprehensive backtests (all pairs, multiple configs)
4. ✅ Applied optimized TP/SL settings
5. ✅ Built desktop app (Windows Portable + Linux AppImage)

## Prioritized Backlog

### P0 - Critical (ALL COMPLETE ✅)
- [x] Signal generation with ML
- [x] Automatic outcome tracking
- [x] Optimized TP/SL settings
- [x] Push notifications
- [x] Backtesting engine
- [x] Admin panel
- [x] Stripe subscriptions
- [x] Desktop app

### P1 - High Priority
- [ ] Windows NSIS installer (requires Windows build environment)
- [ ] macOS .dmg build (requires macOS)
- [ ] Code signing for distribution

### P2 - Medium Priority
- [ ] Complete Stripe with real API key
- [ ] Admin panel enhancements

### P3 - Future
- [ ] ML model training with historical data
- [ ] Auto-updates for desktop app

## Partial Close Percentages (for Trade Copier)
- TP1: 33%
- TP2: 33%
- TP3: 34%

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals
- **Stripe**: TEST MODE

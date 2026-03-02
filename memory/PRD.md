# Grandcom Forex Signals Pro - Product Requirements Document

## Original Problem Statement
Build a professional Forex & Gold (XAUUSD) signals mobile app named "Grandcom Forex Signals Pro". The core feature is a fully automatic signal generation system that posts trading signals to a Telegram channel.

## Platforms
- **Mobile Web App**: React Native (Expo) - LIVE
- **Desktop App**: Electron - BUILT (Windows Portable + Linux AppImage)

## Trading Pairs Status

### Active Pairs (10)
| Pair | TP1 | TP2 | TP3 | SL | Status |
|------|-----|-----|-----|-----|--------|
| XAUUSD | 7 | 15 | 25 | ATR | ⭐ Optimized |
| XAUEUR | 5 | 10 | 15 | ATR | ⭐ Top Performer |
| USDJPY | 3 | 6 | 9 | 10 | ⭐ 73.5% WR |
| GBPUSD | 3 | 6 | 9 | 10 | ⭐ 68.8% WR |
| EURUSD | 3 | 6 | 9 | 10 | OK |
| USDCAD | 3 | 6 | 9 | 10 | OK |
| USDCHF | 3 | 6 | 9 | 10 | OK |
| EURJPY | 3 | 6 | 9 | 10 | OK |
| GBPJPY | 3 | 6 | 9 | 10 | OK |
| AUDUSD | 2 | 4 | 6 | 8 | Adjusted |

### Disabled Pairs (1)
| Pair | Reason |
|------|--------|
| BTCUSD | 17.5% WR, PF 0.14 - Too volatile |

## All Features - COMPLETE ✅

### Signal Generation
- Automatic ML-powered signal generation
- 10 active trading pairs (BTCUSD disabled)
- Optimized TP/SL settings per pair
- Real-time Telegram posting

### Admin Panel Features
- Manual Signal Creation
- User Management (role/tier editing)
- ML Performance Dashboard
- System Configuration View

### ML Model Optimization
- Performance analysis by pair/regime
- Pair ranking and recommendations
- Auto-disable underperformers

### Other Features
- Stripe Subscriptions (backend + frontend)
- Push Notifications
- Historical Backtesting (3-10 years)
- Signal Outcome Tracking

## Session Changes (March 2, 2026)

### BTCUSD Disabled
- Win Rate: 17.5%
- Profit Factor: 0.14
- Reason: Extremely volatile, consistent losses

### AUDUSD Adjusted
- Old: 3/6/9 pips (23.6% WR)
- New: 2/4/6 pips, SL=8 (Ultra-conservative)
- Goal: Capture quick wins with tighter targets

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main API with pair filtering
│   ├── ml_engine/
│   │   └── model_trainer.py         # ML optimization
│   └── ...
├── frontend/
│   └── app/(tabs)/admin.tsx         # Enhanced admin panel
└── desktop/
    └── dist/                        # Built apps
```

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals

## Partial Close Percentages
- TP1: 33%
- TP2: 33%
- TP3: 34%

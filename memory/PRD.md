# Grandcom Forex Signals Pro - Product Requirements Document

## Original Problem Statement
Build a professional Forex & Gold (XAUUSD) signals mobile app named "Grandcom Forex Signals Pro". The core feature is a fully automatic signal generation system that posts trading signals to a Telegram channel with automatic profit-taking and trade closure.

## Platforms
- **Mobile Web App**: React Native (Expo) - LIVE
- **Desktop App**: Electron - BUILT (Windows Portable + Linux AppImage)

## All Features - COMPLETE ✅

### Signal Generation
- Automatic signal generation with ML-powered analysis
- 11 trading pairs: XAUUSD, XAUEUR, BTCUSD, EURUSD, GBPUSD, USDJPY, EURJPY, GBPJPY, AUDUSD, USDCAD, USDCHF
- Optimized TP/SL settings based on 2020-2024 backtesting

### Admin Panel Enhancements (March 2026) - NEW
- **Manual Signal Creation**: Create custom signals with pair selection, BUY/SELL, entry price, TP1/TP2/TP3, SL
- **User Management**: Change user roles (USER/PREMIUM/ADMIN), subscription tiers (FREE/PRO/PREMIUM), delete users
- **ML Performance Dashboard**: View pair rankings, regime performance, recommendations
- **Quick Actions**: Create Signal, Check Outcomes, Run Backtest, Manage Users

### ML Model Optimization (March 2026) - NEW
- Performance analysis by pair and regime
- Pair ranking by win rate and profit factor
- Automatic recommendations for TP/SL adjustments
- API endpoints: `/admin/ml/performance`, `/admin/ml/optimize`

### Current Live Performance (500 signals analyzed):
| Pair | Win Rate | Profit Factor | Status |
|------|----------|---------------|--------|
| XAUEUR | 80.0% | 1.44 | ⭐ TOP |
| USDJPY | 73.5% | 1.90 | ⭐ TOP |
| USDCHF | 72.2% | 1.13 | ⭐ TOP |
| GBPUSD | 68.8% | 1.85 | ⭐ TOP |
| EURUSD | 61.3% | 1.06 | OK |
| USDCAD | 51.2% | 1.26 | OK |
| XAUUSD | 48.2% | 0.84 | REVIEW |
| EURJPY | 40.9% | 0.47 | REVIEW |

### OPTIMIZED TP/SL Settings
**FOREX Pairs - Conservative (3/6/9 pips, SL=10):**
- Average profit factor improved by ~11%

**GOLD Pairs:**
- XAUUSD: 7/15/25 pips (Balanced)
- XAUEUR: 5/10/15 pips (Current)

### Other Completed Features
- Stripe Subscription System (backend + frontend)
- Push Notifications (Expo)
- Historical Backtesting (3-10 years)
- JWT Authentication with Role-based Admin
- Telegram Integration (@grandcomsignals)
- Signal Outcome Tracking (auto TP/SL detection)

## Technical Architecture

```
/app
├── backend/
│   ├── server.py                    # Main FastAPI with all endpoints
│   ├── subscription_service.py      # Stripe subscriptions
│   ├── signal_outcome_tracker.py    # Auto TP/SL monitoring
│   ├── notification_service.py      # Push notifications
│   ├── backtest_engine.py           # Historical backtesting
│   └── ml_engine/
│       ├── regime_detector.py       # Market regime ML
│       ├── signal_optimizer.py      # Signal optimization
│       └── model_trainer.py         # ML training & analysis (NEW)
├── frontend/
│   └── app/(tabs)/
│       ├── admin.tsx                # Enhanced admin panel (NEW)
│       ├── subscription.tsx         # Subscription plans
│       └── ...
└── desktop/
    └── dist/                        # Built apps
        ├── *.AppImage               # Linux
        └── *.zip                    # Windows Portable
```

## Key API Endpoints (Admin)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/signals/create` | POST | Create manual signal |
| `/admin/users/{id}` | PUT | Update user role/tier |
| `/admin/users/{id}` | DELETE | Delete user |
| `/admin/ml/performance` | GET | ML performance analysis |
| `/admin/ml/optimize` | POST | Run ML optimization |
| `/admin/pair-config` | GET | Get pair configurations |

## Session Accomplishments (March 2, 2026)

1. ✅ Fixed Auth Context role synchronization
2. ✅ Completed Stripe subscription system
3. ✅ Ran comprehensive backtests & optimized settings
4. ✅ Built desktop app (Windows + Linux)
5. ✅ **Admin Panel Enhancements**:
   - Manual signal creation UI
   - User management with role/tier editing
   - Delete user functionality
6. ✅ **ML Model Optimization**:
   - Created model_trainer.py
   - Performance analysis by pair/regime
   - API endpoints for ML insights

## Prioritized Backlog

### P0 - Critical (ALL COMPLETE ✅)
- [x] All signal generation features
- [x] All admin panel features
- [x] ML optimization & analysis
- [x] Desktop app builds

### P1 - High Priority
- [ ] Investigate AUDUSD/BTCUSD poor performance
- [ ] Add filtering/sorting to signal history

### P2 - Medium Priority
- [ ] Real Stripe API key for production
- [ ] Windows NSIS installer (requires Windows)

### P3 - Future
- [ ] macOS build
- [ ] Auto-update for desktop

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals
- **Stripe**: TEST MODE

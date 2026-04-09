# Grandcom Forex Signals Pro - Product Requirements Document

## Overview
Professional Forex & Gold (XAUUSD) signals mobile app with fully automatic signal generation system.

## Active Trading Pairs (21 pairs) - Updated March 10, 2025

### Major Pairs
| Pair | TP1 | TP2 | TP3 | SL | Status |
|------|-----|-----|-----|-----|--------|
| XAUUSD | 7 | 15 | 25 | ATR | ✅ ACTIVE |
| XAUEUR | 5 | 10 | 15 | ATR | ✅ ACTIVE |
| EURUSD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| GBPUSD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| USDJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |

### Cross Pairs
| Pair | TP1 | TP2 | TP3 | SL | Status |
|------|-----|-----|-----|-----|--------|
| EURJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| GBPJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| AUDUSD | 2 | 4 | 6 | 8 | ✅ ACTIVE |
| USDCAD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| USDCHF | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| NZDUSD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| AUDJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| CADJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |

### New Institutional Pairs
| Pair | TP1 | TP2 | TP3 | SL | Status |
|------|-----|-----|-----|-----|--------|
| CHFJPY | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| EURAUD | 4 | 8 | 12 | 12 | ✅ ACTIVE |
| GBPCAD | 4 | 8 | 12 | 12 | ✅ ACTIVE |
| EURCAD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| GBPAUD | 4 | 8 | 12 | 12 | ✅ ACTIVE |
| AUDNZD | 3 | 6 | 9 | 10 | ✅ ACTIVE |
| EURGBP | 2 | 4 | 6 | 8 | ✅ ACTIVE |
| EURCHF | 2 | 4 | 6 | 8 | ✅ ACTIVE |

## Disabled Pairs
| Pair | Reason |
|------|--------|
| BTCUSD | 17.5% win rate - too volatile |

## Trading Strategy (Updated March 10, 2025)

### Regime-Based Direction Enforcement
| Market Regime | Signal Direction | Strategy |
|---------------|------------------|----------|
| TREND_UP | BUY only | Follow the trend |
| TREND_DOWN | SELL only | Follow the trend |
| RANGE | Both BUY/SELL | Mean reversion |
| HIGH_VOL | Both BUY/SELL | Breakout strategy |

### Session Restrictions: **REMOVED**
- All pairs now trade 24/7
- No time-based filtering

### Confidence Settings
| Setting | Value |
|---------|-------|
| Min Confidence | 60% |
| Min Regime Confidence | 55% |
| High Confidence | 70% |

## Deployment Status

### Railway (Production)
- **URL**: https://railway.com/project/a38415b0-428a-4149-a3ca-3c0a720df974
- **Status**: ✅ ONLINE
- **Services**:
  - grandcom-forex-signals: ✅ Online
  - MongoDB: ✅ Online

### Emergent (Preview/Development)
- **URL**: https://gold-signal-debug.preview.emergentagent.com
- **Status**: ✅ Working

## Technical Stack
- **Frontend:** React Native, Expo
- **Backend:** FastAPI, Python 3.11
- **Database:** MongoDB
- **ML:** scikit-learn, hmmlearn
- **Integrations:** Twelve Data API, Telegram Bot, OpenAI

## Key Fixes Applied (March 10, 2025)
1. ✅ Removed session restrictions
2. ✅ Regime-based direction (Uptrend=BUY, Downtrend=SELL)
3. ✅ XAUUSD/XAUEUR consistency fixed
4. ✅ pydantic-core>=2.27.0 for Python 3.13 compatibility
5. ✅ Confidence threshold lowered to 60%
6. ✅ Added 8 new institutional pairs

## Admin Credentials
- **Email**: admin@forexsignals.com
- **Password**: Admin@2024!Forex

## Telegram Channel
- @grandcomsignals

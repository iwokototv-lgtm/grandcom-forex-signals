# Grandcom Forex Signals Pro - Product Requirements Document

## Profitability Filters (Implemented March 2026)

### 1. Regime Filter ✅
- **Only trade**: TREND_UP, TREND_DOWN
- **Skip**: RANGE (48% WR), VOLATILE
- **Impact**: +25% expected win rate improvement

### 2. Confidence Threshold ✅
- **Min AI Confidence**: 70%
- **Min Regime Confidence**: 65%
- **Impact**: Filters out low-probability setups

### 3. Session Filter ✅
| Pair | Optimal Hours (UTC) |
|------|---------------------|
| EURUSD, GBPUSD, XAUEUR | 8:00-16:00 (London) |
| XAUUSD | 8:00-20:00 (London + NY) |
| USDJPY, USDCAD | 13:00-21:00 (New York) |
| EURJPY, GBPJPY | 8:00-21:00 (Overlap) |
| AUDUSD | 0:00-8:00 + 13:00-21:00 |

### 4. Drawdown Protection ✅
- **Max daily losses**: 3 per pair
- **Max daily loss pips**: 50 per pair
- **Pause duration**: 4 hours after hitting limit

## Trading Pairs Status

### Active Pairs (10)
| Pair | TP1 | TP2 | TP3 | SL | Status |
|------|-----|-----|-----|-----|--------|
| XAUUSD | 7 | 15 | 25 | ATR | Monitoring |
| XAUEUR | 5 | 10 | 15 | ATR | ⭐ Top Performer |
| USDJPY | 3 | 6 | 9 | 10 | ⭐ High WR |
| GBPUSD | 3 | 6 | 9 | 10 | ⭐ High WR |
| EURUSD | 3 | 6 | 9 | 10 | OK |
| USDCAD | 3 | 6 | 9 | 10 | OK |
| USDCHF | 3 | 6 | 9 | 10 | OK |
| EURJPY | 3 | 6 | 9 | 10 | OK |
| GBPJPY | 3 | 6 | 9 | 10 | OK |
| AUDUSD | 2 | 4 | 6 | 8 | Adjusted |

### Disabled Pairs (1)
| Pair | Reason |
|------|--------|
| BTCUSD | 17.5% WR, PF 0.14 |

## Admin API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/admin/filters` | View all filter settings |
| `GET /api/admin/filter-stats` | View filter impact stats |
| `GET /api/admin/ml/performance` | ML performance by pair |
| `GET /api/admin/system-config` | System configuration |

## Filter Log Examples
```
📉 XAUUSD skipped - RANGE regime has lower win rate
📉 EURUSD skipped - RANGE regime has lower win rate
⏰ GBPUSD skipped - not in optimal session
🛑 AUDUSD paused - Max daily losses (3) reached
📊 USDJPY skipped - confidence 65% < 70% threshold
```

## Expected Impact
- **Win Rate**: +15-25% improvement
- **Fewer Signals**: ~40-50% fewer signals (quality > quantity)
- **Drawdown**: Reduced maximum drawdown with auto-pause

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals

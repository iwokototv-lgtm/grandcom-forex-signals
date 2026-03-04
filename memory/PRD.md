# Grandcom Forex Signals Pro - Final Configuration

## Active Trading Pairs (9 pairs)

| Pair | TP1 | TP2 | TP3 | SL | Live WR | Live PF | Sessions |
|------|-----|-----|-----|-----|---------|---------|----------|
| EURUSD | 3 | 6 | 9 | 10 | 86.8% | 2.73 | London |
| USDCHF | 3 | 6 | 9 | 10 | 83.8% | 1.71 | London |
| USDJPY | 3 | 6 | 9 | 10 | 80.6% | 2.72 | Asian + NY |
| EURJPY | 3 | 6 | 9 | 10 | 80.0% | 2.68 | ALL (22h) |
| USDCAD | 3 | 6 | 9 | 10 | 78.0% | 1.72 | NY |
| GBPUSD | 3 | 6 | 9 | 10 | 76.5% | 2.60 | London |
| NZDUSD | 3 | 6 | 9 | 10 | NEW | NEW | Asian + NY |
| AUDJPY | 3 | 6 | 9 | 10 | NEW | NEW | Asian + NY |
| CADJPY | 3 | 6 | 9 | 10 | NEW | NEW | Asian + NY |

## Disabled Pairs (5 pairs)

| Pair | Reason |
|------|--------|
| XAUUSD | -5619.8 pips, 53.7% WR, PF 0.56 |
| XAUEUR | -8436.5 pips, 60.4% WR, PF 0.35 |
| GBPJPY | -1115.0 pips, 65.1% WR, PF 0.26 |
| AUDUSD | -596.6 pips, 34.6% WR, PF 0.34 |
| BTCUSD | -54684.0 pips, 14.8% WR, PF 0.18 |

## Session Schedule

### 🌏 Asian Session (0:00-8:00 UTC) - 6 pairs
- USDJPY ⭐ (80.6% WR)
- EURJPY ⭐ (80.0% WR)
- NZDUSD (NEW)
- AUDJPY (NEW)
- CADJPY (NEW)

### 🇬🇧 London Session (8:00-16:00 UTC) - 4 pairs
- EURUSD ⭐ (86.8% WR)
- USDCHF ⭐ (83.8% WR)
- GBPUSD (76.5% WR)
- EURJPY (80.0% WR)

### 🇺🇸 New York Session (13:00-21:00 UTC) - 7 pairs
- USDJPY ⭐ (80.6% WR)
- USDCAD ⭐ (78.0% WR)
- EURJPY (80.0% WR)
- NZDUSD (NEW)
- AUDJPY (NEW)
- CADJPY (NEW)

### 🌐 All Sessions - 1 pair
- EURJPY (22 hours/day coverage)

## Profitability Filters

| Filter | Setting | Purpose |
|--------|---------|---------|
| Regime Filter | Skip RANGE, VOLATILE | Only trade trends |
| Confidence | Min 55% AI, 60% Regime | Quality signals only |
| Session | Per-pair hours | Optimal liquidity |
| Drawdown | Max 3 losses, 50 pips/day | Risk management |

## Technical Configuration

- **Symbol mappings**: All 14 pairs configured for Twelve Data API
- **Backtest engine**: Updated with new pairs (NZDUSD, AUDJPY, CADJPY)
- **Admin endpoints**: /admin/filters, /admin/filter-stats available

## Deployment Checklist

- [x] Active pairs optimized based on LIVE performance
- [x] Unprofitable pairs disabled
- [x] Session filters configured
- [x] New Asian pairs added (NZDUSD, AUDJPY, CADJPY)
- [x] Symbol mappings updated for API
- [x] Backtest engine updated
- [x] Confidence threshold adjusted (70% → 55%)
- [x] All filters working

## Credentials
- **Admin**: admin@forexsignals.com / Admin@2024!Forex
- **Telegram**: @grandcomsignals

# Deployment Manifest — v3.0

## Files Created/Modified

### Core Application
| File | Status | Description |
|------|--------|-------------|
| `backend/gold_server_v3.py` | ✅ NEW | FastAPI app with 11 endpoints |
| `backend/config.py` | ✅ NEW | Centralized configuration (50+ vars) |

### ML Engine (16 modules)
| File | Status | Description |
|------|--------|-------------|
| `backend/ml_engine/regime_detector.py` | ✅ EXISTS | G3: Market regime detection |
| `backend/ml_engine/smc_ict_strategy.py` | ✅ NEW | SMC/ICT institutional strategy |
| `backend/ml_engine/mean_reversion_strategy.py` | ✅ NEW | Mean reversion strategy |
| `backend/ml_engine/multi_timeframe_confirmation.py` | ✅ NEW | G2: MTF confirmation |
| `backend/ml_engine/pivot_points_analyzer.py` | ✅ NEW | G1: Daily pivot points |
| `backend/ml_engine/correlation_engine.py` | ✅ NEW | Correlation/exposure engine |
| `backend/ml_engine/risk_parity.py` | ✅ NEW | Risk parity allocation |
| `backend/ml_engine/volatility_adjustment.py` | ✅ NEW | Dynamic position sizing |
| `backend/ml_engine/drawdown_recovery.py` | ✅ NEW | Drawdown recovery management |
| `backend/ml_engine/economic_calendar.py` | ✅ NEW | Economic event filtering |
| `backend/ml_engine/performance_attribution.py` | ✅ NEW | Performance tracking |
| `backend/ml_engine/trade_journal.py` | ✅ NEW | Trade analysis |
| `backend/ml_engine/position_calculator.py` | ✅ NEW | Position sizing |
| `backend/ml_engine/portfolio_manager.py` | ✅ NEW | Portfolio management |
| `backend/ml_engine/strategy_router.py` | ✅ NEW | Signal routing |
| `backend/ml_engine/hybrid_portfolio_system_v3.py` | ✅ NEW | System integration |
| `backend/ml_engine/feature_engineering.py` | ✅ EXISTS | ML feature extraction |
| `backend/ml_engine/smart_money.py` | ✅ EXISTS | Legacy SMC (preserved) |
| `backend/ml_engine/multi_timeframe.py` | ✅ EXISTS | Legacy MTF (preserved) |
| `backend/ml_engine/__init__.py` | ✅ UPDATED | Updated exports |

### Infrastructure
| File | Status | Description |
|------|--------|-------------|
| `railway.json` | ✅ UPDATED | Railway deployment config (Dockerfile builder) |
| `Dockerfile` | ✅ NEW | Python 3.11 Docker image |
| `docker-compose.yml` | ✅ NEW | Local development setup |
| `requirements.txt` | ✅ UPDATED | Python dependencies |
| `.env.example` | ✅ NEW | Environment variables template |

### Documentation
| File | Status | Description |
|------|--------|-------------|
| `README.md` | ✅ UPDATED | System overview |
| `DEPLOYMENT.md` | ✅ NEW | Deployment guide |
| `SYSTEM_SUMMARY.md` | ✅ NEW | System summary |
| `DEPLOYMENT_MANIFEST.md` | ✅ NEW | This file |
| `COMPLETE_DELIVERY.md` | ✅ NEW | Delivery summary |

## Backward Compatibility

The original `gold_server.py` (v2.0) is preserved and unchanged. The new `gold_server_v3.py` is a separate file that can be deployed independently.

To switch between versions, update `railway.json`:
- v3.0: `"startCommand": "uvicorn gold_server_v3:app --host 0.0.0.0 --port ${PORT:-8002}"`
- v2.0: `"startCommand": "uvicorn gold_server:app --host 0.0.0.0 --port ${PORT:-8002}"`

## Database

- **v2.0 collection:** `gold_signals` (unchanged)
- **v3.0 collection:** `gold_signals` (same, with additional fields: regime, smc_score, mtf_alignment, pivot_zone, system_version)
- **DB name:** `gold_signals_v3` (configurable via `DB_NAME` env var)

## API Compatibility

All v2.0 endpoints are preserved in v3.0:
- `GET /api/health` — Enhanced with system_components count
- `GET /api/signals` — Enhanced with pair filter

New v3.0 endpoints:
- `GET /api/system/status`
- `GET /api/analysis/regime/{pair}`
- `GET /api/analysis/smc/{pair}`
- `GET /api/analysis/pivots/{pair}`
- `GET /api/analysis/mtf/{pair}`
- `GET /api/analysis/hybrid/{pair}`
- `GET /api/portfolio/state`
- `GET /api/performance`
- `POST /api/signals/trigger`

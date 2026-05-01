# Grandcom Gold Signals — Migration Package

## What This Is

This directory contains all files needed to set up the **Gold service** (`serene-growth`) as a completely independent repository: `iwokototv-lgtm/grandcom-gold-signals`.

## Why This Migration Is Critical

The Gold service and Forex service currently share the same GitHub repository (`grandcom-forex-signals`). This causes:

- ❌ Editing Forex code triggers Gold service rebuild
- ❌ Merging any code affects all services unpredictably
- ❌ Rescanner cannot run independently
- ❌ Unintended deployments cause financial losses

After migration, each service has its own repo and deploys **completely independently**.

---

## Files in This Package

```
gold-migration/
├── README.md               ← This file
├── Procfile                ← Railway process definition
├── nixpacks.toml           ← Railway build config (Python 3.11)
├── .env.example            ← All required environment variables
└── backend/
    ├── gold_server.py      ← Main Gold server (Elite Edition v4)
    └── requirements.txt    ← Python dependencies
```

---

## Migration Steps

### Step 1 — Create the new GitHub repo (already done)
The repo `iwokototv-lgtm/grandcom-gold-signals` should already exist.

### Step 2 — Push this code to the new repo

```bash
# Clone the new empty repo
git clone https://github.com/iwokototv-lgtm/grandcom-gold-signals.git
cd grandcom-gold-signals

# Copy files from this migration package
# (copy the contents of gold-migration/ into the repo root)
cp -r /path/to/gold-migration/backend ./backend
cp /path/to/gold-migration/Procfile ./Procfile
cp /path/to/gold-migration/nixpacks.toml ./nixpacks.toml
cp /path/to/gold-migration/.env.example ./.env.example
cp /path/to/gold-migration/README.md ./README.md

# Commit and push
git add .
git commit -m "feat: initial Gold service — migrated from grandcom-forex-signals"
git push origin main
```

### Step 3 — Update Railway Gold service

1. Go to Railway dashboard → **serene-growth** service
2. Settings → **Source** → Change repository to `iwokototv-lgtm/grandcom-gold-signals`
3. Set **Root Directory** to `backend`
4. Set **Watch Paths** to `backend/**` (or leave blank)
5. Verify all environment variables are set (see `.env.example`)
6. Trigger a manual deploy and confirm health check passes

### Step 4 — Verify the service is running

```bash
curl https://your-gold-service.railway.app/api/health
# Expected: {"status":"ok","service":"gold_signals_elite",...}
```

### Step 5 — Clean up the old repo (optional but recommended)

Once the Gold service is confirmed working on the new repo:

1. Delete the `gold/` directory from `grandcom-forex-signals`
2. Delete `backend/gold_server (2).py` from `grandcom-forex-signals`
3. Delete `backend/gold_server.py` from `grandcom-forex-signals` (if not used by Forex)
4. Delete `backend/gold_Procfile` and `backend/gold_requirements.txt`

---

## Railway Service Configuration

| Setting | Value |
|---------|-------|
| Repository | `iwokototv-lgtm/grandcom-gold-signals` |
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn gold_server:app --host 0.0.0.0 --port $PORT` |
| Runtime | Python 3.11 |

---

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `MONGO_URL` | MongoDB connection string |
| `DB_NAME` | Database name (default: `gold_signals`) |
| `TELEGRAM_BOT_TOKEN` | Bot token for @grandcomgold |
| `TELEGRAM_GOLD_CHANNEL_ID` | Channel ID (e.g. `@grandcomgold`) |
| `TWELVE_DATA_API_KEY` | Twelve Data API key for price feeds |
| `OPENAI_API_KEY` | OpenAI key for AI signal analysis |

See `.env.example` for the full list with descriptions.

---

## Gold Server Features (Elite Edition v4)

### Signal Engine
- **XAUUSD** and **XAUEUR** — 4H swing signals
- Grouped indicator scoring: G1 Trend (40%) + G2 Momentum (30%) + G3 Trigger (30%)
- AI confidence assessment via GPT-4o-mini
- Minimum score: 60/100 | High Conviction: 85/100

### Safety Gates (10 layers)
1. News Guard — blocks trades near high-impact events
2. H4 Multi-Timeframe alignment
3. DXY Correlation Engine
4. Candlestick Price Action
5. Flash Crash Circuit Breaker
6. Session-based confidence filter
7. Shannon Entropy (chaos detection)
8. Keltner Channel extension filter
9. OBV Volume-Price Divergence
10. VW-MACD volume confirmation

### Risk Management
- ATR-based dynamic SL/TP
- Drawdown protection (max 2 losses/day)
- Breakeven monitor (moves SL to entry after TP1)
- ATR trailing stop (2.5×ATR after breakeven)
- Per-pair throttle (6h between signals)

### Monitoring
- Black box JSONL trade log
- CSV denial log (all blocked signals)
- Outcome tracker (auto-closes TP/SL hits)
- REST API endpoints for signals, stats, breakeven

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Service health check |
| `GET /api/gold/signals` | List signals (filter by status) |
| `GET /api/gold/stats` | Win rate, totals, throttle state |
| `GET /api/gold/breakeven` | Active breakeven signals |

# Grandcom Gold Signals — Consolidated Migration Package

## What This Directory Contains

`gold-consolidated/` is the **single source of truth** for all Gold service files, assembled from every scattered location in this repo:

| Source | Destination |
|--------|-------------|
| `backend/gold_server (2).py` | `backend/gold_server.py` ← **the one actually running** (Elite Edition v4) |
| `backend/gold_requirements.txt` | `backend/requirements.txt` |
| `backend/gold_Procfile` | `Procfile` |
| `gold/` directory | superseded by v4 — not included |
| `gold-migration/` | superseded by this package |
| `grandcom-gold-signals/` | superseded by this package |

### Directory Structure

```
gold-consolidated/
├── README.md               ← This file (migration guide)
├── Procfile                ← Railway process definition
├── nixpacks.toml           ← Railway build config (Python 3.11)
├── .env.example            ← All required environment variables
└── backend/
    ├── gold_server.py      ← Main Gold server (Elite Edition v4, 1800+ lines)
    └── requirements.txt    ← Gold-specific Python dependencies
```

---

## Gold Server Features (Elite Edition v4)

### Signal Engine
- **Pairs**: XAUUSD and XAUEUR — 4H swing signals
- **Grouped indicator scoring**: G1 Trend (40%) + G2 Momentum (30%) + G3 Trigger (30%)
- **AI confidence assessment** via GPT-4o-mini (litellm + emergentintegrations fallback)
- **Minimum score**: 60/100 | **High Conviction**: 85/100

### Safety Gates (14 layers)
1. **News Guard** — blocks trades ±60 min around high-impact events
2. **H4 Multi-Timeframe alignment** — H4 trend must agree with signal direction
3. **DXY Correlation Engine** — strong USD blocks Gold BUYs
4. **Candlestick Price Action** — Hammer, Engulfing, Pin Bar, Doji detection
5. **Flash Crash Circuit Breaker** — halts if price moves >$15 in <2 min
6. **Session-Based Confidence Filter** — higher bar during Asian/Dead Zone sessions
7. **Shannon Entropy Filter** — blocks chaotic/random markets
8. **Hurst Exponent** — regime detection (trending vs mean-reverting)
9. **Keltner Channel Extension** — blocks BUY at KC upper, SELL at KC lower
10. **OBV Volume-Price Divergence** — filters exhaustion moves
11. **Liquidity Sweep Detection** — identifies stop hunts
12. **Gold-Silver Ratio** — reduces BUY conviction when GSR > 80
13. **Volume-Weighted MACD** — confirms institutional participation
14. **Black Box Logging** — every signal attempt logged to JSONL + CSV

### Risk Management
- ATR-based dynamic SL/TP (0.4×ATR SL, 0.5/1.0/1.5×ATR TP1/2/3)
- Drawdown protection (max 2 losses/day, 12h pause)
- Breakeven monitor (moves SL to entry after TP1 hit)
- ATR trailing stop (2.5×ATR after breakeven)
- Per-pair throttle (6h between signals)
- Duplicate guard (no duplicate ACTIVE signals per pair)

### API Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Service health check |
| `GET /api/gold/signals` | List signals (optional `?status=ACTIVE`) |
| `GET /api/gold/stats` | Win rate, active count, throttle state |
| `GET /api/gold/breakeven` | Signals with breakeven triggered |

---

## Migration Steps

### Step 1 — Create the new GitHub repo

1. Go to https://github.com/new
2. **Repository name**: `grandcom-gold-signals`
3. **Owner**: `iwokototv-lgtm`
4. **Visibility**: Private
5. **Do NOT** initialize with README
6. Click **Create repository**

### Step 2 — Push this code to the new repo

```bash
# Clone the new empty repo
git clone https://github.com/iwokototv-lgtm/grandcom-gold-signals.git
cd grandcom-gold-signals

# Copy the contents of gold-consolidated/ into the repo root
# (copy everything INSIDE gold-consolidated/, not the folder itself)
cp /path/to/grandcom-forex-signals/gold-consolidated/Procfile ./Procfile
cp /path/to/grandcom-forex-signals/gold-consolidated/nixpacks.toml ./nixpacks.toml
cp /path/to/grandcom-forex-signals/gold-consolidated/.env.example ./.env.example
cp /path/to/grandcom-forex-signals/gold-consolidated/README.md ./README.md
cp -r /path/to/grandcom-forex-signals/gold-consolidated/backend ./backend

# Commit and push
git add .
git commit -m "feat: initial Gold service — Elite Edition v4 standalone"
git push origin main
```

### Step 3 — Update Railway Gold service

1. Go to Railway dashboard → **serene-growth** service
2. Click **Settings** → **Source**
3. Change **Repository** to: `iwokototv-lgtm/grandcom-gold-signals`
4. Set **Branch**: `main`
5. Set **Root Directory**: `backend/`
6. Click **Save**

### Step 4 — Set environment variables

In Railway dashboard → Gold service → **Variables** tab, set:

```
MONGO_URL=<your MongoDB connection string>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=<your Telegram bot token>
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=<your TwelveData API key>
OPENAI_API_KEY=<your OpenAI API key>
```

See `.env.example` for full descriptions.

### Step 5 — Verify the deployment

```bash
# Watch Railway build logs — should see:
# 🥇 Gold Signals Server — Institutional Elite Edition
#    Pairs     : ['XAUUSD', 'XAUEUR'] | Interval: 240min

# Check health endpoint
curl https://your-gold-service.up.railway.app/api/health
# Expected: {"status":"ok","service":"gold_signals_elite",...}
```

### Step 6 — Clean up this repo (after Gold is confirmed working)

Once the Gold service is confirmed running from `grandcom-gold-signals`:

1. Delete `gold/` directory from this repo
2. Delete `backend/gold_server (2).py`
3. Delete `backend/gold_server.py` (if not used by Forex)
4. Delete `backend/gold_Procfile`
5. Delete `backend/gold_requirements.txt`
6. Delete `gold-migration/` directory
7. Delete `grandcom-gold-signals/` directory
8. Delete `gold-consolidated/` directory (this one)

---

## Railway Service Configuration

| Setting | Value |
|---------|-------|
| Repository | `iwokototv-lgtm/grandcom-gold-signals` |
| Branch | `main` |
| Root Directory | `backend/` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn gold_server:app --host 0.0.0.0 --port $PORT` |
| Runtime | Python 3.11 |

---

## Architecture After Separation

```
iwokototv-lgtm/grandcom-forex-signals   ← Forex only
├── backend/
│   ├── server.py          ← Forex main (unchanged)
│   ├── server_final.py
│   ├── ml_engine/         ← Forex ML
│   └── requirements.txt
└── ...

iwokototv-lgtm/grandcom-gold-signals    ← NEW (Gold only)
├── backend/
│   ├── gold_server.py     ← Gold main (Elite Edition v4)
│   ├── nixpacks.toml
│   └── requirements.txt
├── Procfile
├── .env.example
└── README.md
```

Each service deploys **completely independently**. Editing Forex code will never trigger a Gold rebuild, and vice versa.

---

## Troubleshooting

### Build fails: `ModuleNotFoundError`
- Check `backend/requirements.txt` has all dependencies
- Verify Railway Root Directory is set to `backend/`

### No signals being generated
- Check `TWELVE_DATA_API_KEY` is valid (test at twelvedata.com)
- Check `OPENAI_API_KEY` is valid
- Check `MONGO_URL` is correct and MongoDB is accessible
- Look at Railway logs for gate block reasons (News Guard, Session Filter, etc.)

### Telegram not sending
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Verify bot is admin in `@grandcomgold` channel
- Check `TELEGRAM_GOLD_CHANNEL_ID` format (use `@channelname` or numeric ID)

### Health check fails
```bash
curl https://your-service.up.railway.app/api/health
# Expected: {"status":"ok","service":"gold_signals_elite","pairs":["XAUUSD","XAUEUR"],...}
```

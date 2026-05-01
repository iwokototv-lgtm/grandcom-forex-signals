# Grandcom Gold Signals — Railway Deployment Guide

## Overview

This is the **standalone Gold signals service** for XAUUSD & XAUEUR.
It is completely independent from the Forex signals service.

- **Repository**: `iwokototv-lgtm/grandcom-gold-signals`
- **Railway Service**: `serene-growth` (or your Gold service name)
- **Root Directory**: `backend/`
- **Start Command**: `uvicorn gold_server:app --host 0.0.0.0 --port ${PORT:-8001}`

---

## Step 1: Create New GitHub Repository

1. Go to https://github.com/new
2. **Repository name**: `grandcom-gold-signals`
3. **Owner**: `iwokototv-lgtm`
4. **Description**: Gold Trading Service (XAUUSD & XAUEUR)
5. **Visibility**: Private
6. **Do NOT** initialize with README (you'll push this code)
7. Click **Create repository**

---

## Step 2: Push This Code to New Repo

```bash
# Clone the new empty repo
git clone https://github.com/iwokototv-lgtm/grandcom-gold-signals.git
cd grandcom-gold-signals

# Copy all files from this PR's grandcom-gold-signals/ folder
# Then push
git add .
git commit -m "feat: initial gold signals service — standalone"
git push origin main
```

---

## Step 3: Update Railway Gold Service

In the Railway dashboard:

1. Go to your **Gold service** (e.g., `serene-growth`)
2. Click **Settings** → **Source**
3. Change **Repository** to: `iwokototv-lgtm/grandcom-gold-signals`
4. Set **Branch**: `main`
5. Set **Root Directory**: `backend/`
6. Click **Save**

---

## Step 4: Set Environment Variables

In Railway dashboard → your Gold service → **Variables** tab:

```
MONGO_URL=<your MongoDB connection string>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=<your Telegram bot token>
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=<your TwelveData API key>
OPENAI_API_KEY=<your OpenAI API key>
```

---

## Step 5: Verify Build Config

The `backend/nixpacks.toml` file configures the Railway build:

```toml
[phases.setup]
nixPkgs = ["python311Full", "python311Packages.pip"]

[phases.install]
cmds = ["python3 -m pip install -r requirements.txt"]

[start]
cmd = "uvicorn gold_server:app --host 0.0.0.0 --port ${PORT:-8001}"
```

---

## Step 6: Deploy & Verify

1. Trigger a new deployment in Railway
2. Watch the build logs — should see:
   ```
   🥇 Gold Signals Server — Institutional Elite Edition
      Pairs     : ['XAUUSD', 'XAUEUR'] | Interval: 240min
   ```
3. Check the health endpoint:
   ```
   GET https://your-service.up.railway.app/api/health
   ```
4. Verify signals appear in @grandcomgold Telegram channel

---

## Step 7: Clean Up Forex Repo (Optional)

Once Gold is confirmed working from the new repo:

1. In `iwokototv-lgtm/grandcom-forex-signals`, delete:
   - `backend/gold_server.py`
   - `backend/gold_server (2).py`
   - `backend/gold_Procfile`
   - `backend/gold_requirements.txt`
   - `grandcom-gold-signals/` folder (this PR)
2. Commit and push

---

## Architecture After Separation

```
iwokototv-lgtm/grandcom-forex-signals
├── backend/
│   ├── server.py          ← Forex main (unchanged)
│   ├── server_final.py
│   ├── ml_engine/         ← Forex ML
│   └── requirements.txt
└── ...

iwokototv-lgtm/grandcom-gold-signals  ← NEW
├── backend/
│   ├── gold_server.py     ← Gold main
│   ├── ml_engine/         ← Gold ML
│   ├── nixpacks.toml
│   └── requirements.txt
├── Procfile
├── requirements.txt
└── RAILWAY_DEPLOYMENT.md
```

---

## Troubleshooting

### Build fails: `ModuleNotFoundError`
- Check `backend/requirements.txt` has all dependencies
- Verify `backend/nixpacks.toml` points to `requirements.txt`

### No signals being generated
- Check `TWELVE_DATA_API_KEY` is valid
- Check `OPENAI_API_KEY` is valid
- Check `MONGO_URL` is correct
- Look at Railway logs for gate block reasons

### Telegram not sending
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Verify bot is admin in `@grandcomgold` channel
- Check `TELEGRAM_GOLD_CHANNEL_ID` format (use `@channelname` or numeric ID)

### Health check
```bash
curl https://your-service.up.railway.app/api/health
# Expected: {"status":"ok","service":"gold_signals_elite",...}
```

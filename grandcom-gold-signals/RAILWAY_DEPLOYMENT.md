# Railway Deployment Guide — Grandcom Gold Signals

## Overview

This is the **standalone Gold signals service** for XAUUSD & XAUEUR.
It is completely independent from the Forex signals service.

---

## Step 1: Create New GitHub Repository

1. Go to https://github.com/new
2. **Repository name**: `grandcom-gold-signals`
3. **Owner**: `iwokototv-lgtm`
4. **Description**: Gold Trading Service (XAUUSD & XAUEUR)
5. **Visibility**: Private
6. **Do NOT initialize** with README (you'll push this code)
7. Click **Create repository**

---

## Step 2: Push This Code to New Repo

```bash
# Clone the new empty repo
git clone https://github.com/iwokototv-lgtm/grandcom-gold-signals.git
cd grandcom-gold-signals

# Copy all files from grandcom-gold-signals/ folder in this PR
# (or download the files from this PR branch)

# Push to new repo
git add .
git commit -m "feat: initial gold signals service"
git push origin main
```

---

## Step 3: Update Railway Service

In Railway dashboard, update the **Gold service**:

| Setting | Old Value | New Value |
|---------|-----------|-----------|
| Repository | `iwokototv-lgtm/grandcom-forex-signals` | `iwokototv-lgtm/grandcom-gold-signals` |
| Root Directory | `gold/` | `backend/` |
| Start Command | (varies) | `uvicorn gold_server:app --host 0.0.0.0 --port $PORT` |

---

## Step 4: Set Environment Variables

In Railway, set these environment variables for the Gold service:

```
MONGO_URL=<your mongodb connection string>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=<your twelve data key>
OPENAI_API_KEY=<your openai key>
```

---

## Step 5: Verify Deployment

After deploying, check:

```
GET https://your-gold-service.railway.app/api/health
```

Expected response:
```json
{
  "status": "ok",
  "service": "gold_signals",
  "pairs": ["XAUUSD", "XAUEUR"]
}
```

---

## Architecture After Separation

```
grandcom-forex-signals (repo)
└── backend/server.py          → Forex service (EUR/USD, GBP/USD, etc.)

grandcom-gold-signals (repo)
└── backend/gold_server.py     → Gold service (XAUUSD, XAUEUR)
```

✅ No cross-contamination
✅ Independent deployments
✅ Editing Forex won't trigger Gold rebuild

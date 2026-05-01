# Railway Deployment Guide — Grandcom Gold Signals

## 🚀 Deploy in 10 Minutes

### Step 1: Create Railway Service

1. Go to **https://railway.app/new**
2. Click **"Deploy from GitHub repo"**
3. Select `iwokototv-lgtm/grandcom-gold-signals`
4. Railway will auto-detect and start building

### Step 2: Add MongoDB Database

1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"MongoDB"**
3. Railway creates a MongoDB instance automatically

### Step 3: Set Environment Variables

In Railway dashboard → your service → **Variables** tab:

```
MONGO_URL=<copy from MongoDB service connection string>
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=<your Telegram bot token>
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=<your TwelveData API key>
OPENAI_API_KEY=<your OpenAI API key>
```

### Step 4: Configure Root Directory

In Railway dashboard → your service → **Settings** tab:
- **Root Directory**: `backend/`

### Step 5: Get Your Production URL

1. Go to **Settings** → **Networking**
2. Click **"Generate Domain"**
3. You'll get a URL like: `grandcom-gold-signals-production.up.railway.app`

---

## ✅ Verify Deployment

```bash
curl https://your-railway-url.up.railway.app/api/health
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

## 📊 Monitor

- Railway Dashboard → Your Service → **Logs** tab
- Signals run every **2 minutes** automatically
- Check `@grandcomgold` Telegram channel for live signals

---

## 🔧 Troubleshooting

### Build Fails
- Check build logs in Railway
- Ensure `requirements.txt` is in `backend/` folder
- Verify Python version is 3.11

### No Signals Appearing
- Check `TWELVE_DATA_API_KEY` is valid
- Check `OPENAI_API_KEY` is valid
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_GOLD_CHANNEL_ID`
- View logs for specific error messages

### MongoDB Connection Issues
- Verify `MONGO_URL` is correctly copied from MongoDB service
- Check MongoDB service is running in Railway
- Ensure `DB_NAME=gold_signals`

---

## 💰 Railway Pricing

- **Hobby Plan**: $5/month (recommended for this service)
- **Pro Plan**: $20/month (for scaling)

---

## 🔄 Updating the Service

1. Push changes to `main` branch
2. Railway auto-deploys on push
3. Zero downtime deployment

---

## 🏗️ Architecture Notes

This service is **completely independent** from the Forex signals service:
- Separate GitHub repository
- Separate Railway service
- Separate MongoDB database
- Separate Telegram channel (`@grandcomgold`)
- No shared code or dependencies at runtime

Editing this repo will **never** trigger a rebuild of the Forex service.

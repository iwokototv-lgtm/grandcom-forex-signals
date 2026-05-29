# Deployment Guide — Grandcom Gold Signals v3.0

## Railway Deployment (Recommended)

### Prerequisites
- Railway account (Hobby plan or higher)
- MongoDB Atlas cluster (M0 free tier works for development)
- Telegram bot token and channel ID
- TwelveData API key
- OpenAI API key

### Step 1: Prepare Repository

```bash
git clone <your-repo-url>
cd <repo-name>
```

### Step 2: Create Railway Project

1. Go to [railway.app](https://railway.app)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your repository
4. Railway will detect the `railway.json` configuration automatically

### Step 3: Set Environment Variables

In the Railway dashboard, go to your service → **Variables** and add:

**Required:**
```
MONGO_URL=mongodb+srv://...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_GOLD_CHANNEL_ID=...
TWELVE_DATA_API_KEY=...
OPENAI_API_KEY=...
```

**Optional (defaults shown):**
```
DB_NAME=gold_signals_v3
SIGNAL_INTERVAL_MINUTES=30
MIN_CONFIDENCE=65
MTF_MIN_CONFLUENCE=3
ACCOUNT_BALANCE=100000
LOG_LEVEL=INFO
```

See `.env.example` for the complete list.

### Step 4: Deploy

Railway auto-deploys on every push to the main branch. To trigger manually:

```bash
git push origin main
```

### Step 5: Verify Health

Once deployed, check the health endpoint:

```bash
curl https://your-service.railway.app/api/health
```

Expected response:
```json
{
  "status": "ok",
  "service": "grandcom-gold-signals",
  "version": "3.0.0",
  "mongo_connected": true,
  "scheduler_running": true,
  "can_trade": true
}
```

---

## Docker Deployment (Local / VPS)

### Quick Start

```bash
cp .env.example .env
# Edit .env with your credentials
docker-compose up -d
```

### Check Logs

```bash
docker-compose logs -f gold-signals
```

### Stop

```bash
docker-compose down
```

---

## Manual Deployment (VPS)

### Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### Start Server

```bash
cd backend
uvicorn gold_server_v3:app --host 0.0.0.0 --port 8000
```

### With Process Manager (PM2)

```bash
pm2 start "uvicorn gold_server_v3:app --host 0.0.0.0 --port 8000" \
  --name gold-signals \
  --cwd backend
pm2 save
pm2 startup
```

---

## MongoDB Setup

### Atlas (Recommended)

1. Create a free M0 cluster at [mongodb.com/atlas](https://mongodb.com/atlas)
2. Create a database user
3. Whitelist `0.0.0.0/0` (or Railway's IP range)
4. Copy the connection string to `MONGO_URL`

### Collections Created Automatically

- `gold_signals` — Trading signals
- `historical_prices` — Price history for ML training

---

## Telegram Setup

### Create Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions
3. Copy the token to `TELEGRAM_BOT_TOKEN`

### Get Channel ID

1. Add your bot as an admin to your channel
2. Send a message to the channel
3. Visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Find the `chat.id` value (negative number for channels)
5. Set `TELEGRAM_GOLD_CHANNEL_ID` to this value

---

## Monitoring

### Health Check

```bash
curl /api/health
```

### Portfolio Status

```bash
curl /api/portfolio/status
```

### Recent Signals

```bash
curl /api/signals?limit=10
```

### Drawdown Status

```bash
curl /api/drawdown
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `TELEGRAM_BOT_TOKEN not set` | Add the variable in Railway dashboard |
| `MongoDB connection failed` | Check `MONGO_URL` and Atlas IP whitelist |
| `Insufficient 4H data` | TwelveData API key may be invalid or rate-limited |
| `Signal rejected [MTF]` | Normal — insufficient timeframe confluence |
| `Signal rejected [CALENDAR]` | High-impact news event blackout active |
| `can_trade: false` | Check `/api/drawdown` for the pause reason |

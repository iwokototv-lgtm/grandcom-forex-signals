# Deployment Guide — Grandcom Gold Signals v3.0

## Railway Production Deployment

### Prerequisites
- Railway account with production environment (serene-magic)
- MongoDB Atlas cluster (or Railway MongoDB plugin)
- Telegram bot token and channel ID
- TwelveData API key
- OpenAI API key

### Step 1: Connect Repository
1. Go to Railway dashboard → New Project → Deploy from GitHub
2. Select `Dpope704/serene-growth` repository
3. Railway will detect the `Dockerfile` automatically

### Step 2: Configure Environment Variables
In Railway dashboard → Service → Variables, add:

```
MONGO_URL=mongodb+srv://...
DB_NAME=gold_signals_v3
TELEGRAM_BOT_TOKEN=...
TELEGRAM_GOLD_CHANNEL_ID=-1003834233408
TWELVE_DATA_API_KEY=...
OPENAI_API_KEY=...
SIGNAL_INTERVAL_MINUTES=2
MIN_CONFIDENCE=60
DEFAULT_ACCOUNT_BALANCE=10000
ENVIRONMENT=production
```

### Step 3: Deploy
Railway will automatically:
1. Build the Docker image from `Dockerfile`
2. Install Python 3.11 dependencies
3. Start `uvicorn gold_server_v3:app`
4. Run health checks at `/api/health`

### Step 4: Verify Deployment
```bash
# Health check
curl https://your-service.railway.app/api/health

# System status
curl https://your-service.railway.app/api/system/status

# Test signal analysis
curl https://your-service.railway.app/api/analysis/hybrid/XAUUSD
```

## Docker Build Details

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
CMD uvicorn gold_server_v3:app --host 0.0.0.0 --port ${PORT:-8002}
```

## Health Check

The `/api/health` endpoint returns:
```json
{
  "status": "ok",
  "service": "gold_signals_v3",
  "version": "3.0.0",
  "pairs": ["XAUUSD", "XAUEUR"],
  "scheduler_running": true,
  "mongo_connected": true,
  "system_components": 16
}
```

## Monitoring

- **Logs:** Railway dashboard → Service → Logs
- **Metrics:** Railway dashboard → Service → Metrics
- **Alerts:** Configure Railway alerts for service restarts

## Rollback

To rollback to v2.0 (gold_server.py):
1. Railway dashboard → Deployments → Select previous deployment
2. Click "Rollback"

Or update `railway.json` start command:
```json
"startCommand": "uvicorn gold_server:app --host 0.0.0.0 --port ${PORT:-8002}"
```

## Troubleshooting

### Service won't start
- Check all required env vars are set
- Verify MongoDB connection string
- Check Railway logs for import errors

### No signals being generated
- Verify `TWELVE_DATA_API_KEY` is valid
- Check `OPENAI_API_KEY` has credits
- Confirm `TELEGRAM_BOT_TOKEN` is active

### MongoDB connection failed
- Verify `MONGO_URL` format: `mongodb+srv://user:pass@cluster.mongodb.net/`
- Check MongoDB Atlas IP whitelist (allow 0.0.0.0/0 for Railway)

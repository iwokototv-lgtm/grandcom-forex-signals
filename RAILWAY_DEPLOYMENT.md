# Grandcom Forex Signals Pro - Railway Deployment Guide

## 🚀 Quick Deploy (10 minutes)

### Step 1: Create Railway Account
1. Go to **https://railway.app**
2. Click "Login" → "Login with GitHub"
3. Authorize Railway

### Step 2: Push Code to GitHub
1. In Emergent, click **"Save to GitHub"** button
2. Create a new repository: `grandcom-forex-signals`
3. Make it **Private** (recommended)

### Step 3: Deploy on Railway
1. Go to **https://railway.app/new**
2. Click **"Deploy from GitHub repo"**
3. Select your `grandcom-forex-signals` repository
4. Railway will auto-detect and start building

### Step 4: Add MongoDB Database
1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"MongoDB"**
3. Railway creates a MongoDB instance automatically

### Step 5: Set Environment Variables
In Railway dashboard, go to your service → **Variables** tab:

```
MONGO_URL=<copy from MongoDB service - click on it to see connection string>
DB_NAME=forex_signals
JWT_SECRET=your-super-secret-jwt-key-change-this
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
TELEGRAM_BOT_TOKEN=8526275676:AAGC5oSN0KDiXmwiUWrL5RxzGv2-2umCmqA
TELEGRAM_CHANNEL_ID=@grandcomsignals
TWELVE_DATA_API_KEY=7a74d13b2bb448d68f5c348245ae994b
EMERGENT_LLM_KEY=sk-emergent-cA500137aA67f7cC2F
STRIPE_API_KEY=sk_test_emergent
```

### Step 6: Get Your Production URL
1. Go to **Settings** → **Networking**
2. Click **"Generate Domain"**
3. You'll get a URL like: `grandcom-forex-signals-production.up.railway.app`

### Step 7: Update Your Mobile App
Update the APK to use your new production URL:
1. Edit `frontend/.env`:
   ```
   EXPO_PUBLIC_BACKEND_URL=https://your-railway-url.up.railway.app
   ```
2. Rebuild the APK: `eas build -p android --profile preview`

---

## 📱 After Deployment

### Test Your API
```bash
curl https://your-railway-url.up.railway.app/api/health
```

### Monitor Logs
- Railway Dashboard → Your Service → **Logs** tab

### Custom Domain (Optional)
1. Settings → Networking → Custom Domain
2. Add your domain (e.g., api.grandcomfx.com)
3. Update DNS records as instructed

---

## 💰 Railway Pricing
- **Free Tier**: $5 free credits/month
- **Hobby Plan**: $5/month (recommended)
- **Pro Plan**: $20/month (for scaling)

Your app should run fine on the Hobby plan (~$5/month).

---

## 🔧 Troubleshooting

### Build Fails
- Check the build logs in Railway
- Ensure `requirements.txt` is in `/backend/` folder
- Verify Python version is 3.11

### App Crashes
- Check memory usage (Railway dashboard)
- View logs for errors
- May need to upgrade plan if memory exceeds limit

### MongoDB Connection Issues
- Verify MONGO_URL is correctly copied
- Check MongoDB service is running
- Ensure DB_NAME matches

---

## 📞 Support
- Railway Docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway

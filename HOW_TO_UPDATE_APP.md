# 📱 How to Update Your Forex & Gold Signals App

## 🎯 Quick Start Guide

Your app is running at: **https://grandcom-pro-signals.preview.emergentagent.com**

---

## 📂 Project Structure

```
/app/
├── backend/              # FastAPI Backend
│   ├── server.py        # Main backend code (API endpoints, signal generation)
│   ├── .env             # Environment variables (API keys, secrets)
│   └── seed_demo_signals.py  # Demo data generator
│
├── frontend/            # React Native (Expo) Frontend
│   ├── app/             # All screens (file-based routing)
│   │   ├── (auth)/      # Login & Register screens
│   │   ├── (tabs)/      # Main app screens
│   │   │   ├── home.tsx      # Dashboard with stats
│   │   │   ├── signals.tsx   # Signal list
│   │   │   ├── analytics.tsx # Performance charts
│   │   │   └── profile.tsx   # User settings
│   │   └── index.tsx    # Entry point
│   ├── contexts/        # React contexts (AuthContext)
│   ├── utils/          # Helper functions (api.ts)
│   └── .env            # Frontend environment variables
│
└── README_FEATURES.md  # Complete feature documentation
```

---

## 🔧 Common Updates

### 1️⃣ Change Win Rate or Stats

**File:** `/app/backend/seed_demo_signals.py`

**Steps:**
1. Edit the signals array to add more wins/losses
2. For 98% win rate: 49 wins + 1 loss = 50 total closed signals
3. Run the seed script:
```bash
cd /app/backend
python seed_demo_signals.py
```

**Output should show:**
```
✅ Created XX demo signals
📊 Active: X | Wins: 49 | Losses: 1
📈 Win Rate: 98.0%
```

---

### 2️⃣ Add New Trading Pairs

**File:** `/app/backend/server.py`

**Find line ~390:** (in `auto_generate_signals` function)
```python
pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
```

**Add your pair:**
```python
pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP"]
```

**Restart backend:**
```bash
sudo supervisorctl restart backend
```

---

### 3️⃣ Update Telegram Channel/User ID

**File:** `/app/backend/.env`

**Change line:**
```
TELEGRAM_CHANNEL_ID=YOUR_NEW_ID_HERE
```

**Or for channels:**
```
TELEGRAM_CHANNEL_ID=@your_channel_name
```

**Restart backend:**
```bash
sudo supervisorctl restart backend
```

---

### 4️⃣ Change UI Colors or Styling

**Files:** Any file in `/app/frontend/app/(tabs)/`

**Example - Change gold color to blue:**

Find: `color: '#FFD700'` (gold)
Replace with: `color: '#2196F3'` (blue)

**Common color locations:**
- Premium badges: Search for `#FFD700`
- Buy signals: Search for `#4CAF50` (green)
- Sell signals: Search for `#F44336` (red)
- Background: Search for `#0A0E27` (dark blue)

**Restart frontend to see changes:**
```bash
sudo supervisorctl restart expo
```

---

### 5️⃣ Add More TP Levels (TP4, TP5, etc.)

**Backend - File:** `/app/backend/server.py`

**Find the Signal model** (around line 80):
```python
tp_levels: List[float]  # Multiple take profit levels
```

**In signal generation** (around line 280):
```python
tp_levels=ai_analysis["tp_levels"],  # Make sure AI returns 4-5 levels
```

**Frontend - File:** `/app/frontend/app/(tabs)/home.tsx`

**Add more TP rows** (around line 150):
```typescript
<View style={styles.detailRow}>
  <Text style={styles.detailLabel}>TP4</Text>
  <Text style={[styles.detailValue, styles.tpValue]}>
    {signal.tp_levels[3].toFixed(2)}
  </Text>
</View>
```

---

### 6️⃣ Change Subscription Pricing

**File:** `/app/frontend/app/(tabs)/profile.tsx`

**Find line ~62:**
```typescript
'Get unlimited access to all premium signals with higher confidence and better analysis!\\n\\nPrice: $49.99/month',
```

**Change to:**
```typescript
'Get unlimited access to all premium signals with higher confidence and better analysis!\\n\\nPrice: $99/month',
```

---

### 7️⃣ Modify Signal Confidence Threshold

**File:** `/app/backend/server.py`

**Find line ~289:**
```python
is_premium=ai_analysis["confidence"] > 75  # High confidence signals are premium
```

**Change to make more signals premium:**
```python
is_premium=ai_analysis["confidence"] > 70  # Lower threshold = more premium signals
```

---

### 8️⃣ Change Auto-Signal Generation Frequency

**File:** `/app/backend/server.py`

**Find line ~408:**
```python
await asyncio.sleep(900)  # Wait 15 minutes (900 seconds)
```

**Change to generate every 5 minutes:**
```python
await asyncio.sleep(300)  # Wait 5 minutes (300 seconds)
```

**Or every hour:**
```python
await asyncio.sleep(3600)  # Wait 60 minutes (3600 seconds)
```

---

## 🔄 How to Restart Services

### Restart Backend Only:
```bash
sudo supervisorctl restart backend
```

### Restart Frontend Only:
```bash
sudo supervisorctl restart expo
```

### Restart Both:
```bash
sudo supervisorctl restart backend expo
```

### Check Service Status:
```bash
sudo supervisorctl status
```

---

## 🗄️ Database Operations

### View All Signals:
```bash
cd /app/backend
python -c "
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    signals = await db.signals.find().to_list(100)
    print(f'Total signals: {len(signals)}')
    for s in signals[:5]:
        print(f'{s[\"pair\"]} - {s[\"type\"]} - {s.get(\"result\", \"ACTIVE\")}')
    client.close()

asyncio.run(main())
"
```

### Clear All Signals:
```bash
cd /app/backend
python -c "
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    result = await db.signals.delete_many({})
    print(f'Deleted {result.deleted_count} signals')
    client.close()

asyncio.run(main())
"
```

### Reset to Fresh 98% Win Rate Data:
```bash
cd /app/backend
python seed_demo_signals.py
```

---

## 🚀 Testing Changes

### Test Backend API:
```bash
# Test stats endpoint
curl http://localhost:8001/api/stats

# Test signals endpoint (need token)
TOKEN="your_jwt_token_here"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8001/api/signals?limit=5
```

### Test Frontend:
Open in browser: https://grandcom-pro-signals.preview.emergentagent.com

---

## 📝 Add New Screen/Feature

### 1. Create New Screen File
```bash
# Example: Create a news screen
nano /app/frontend/app/(tabs)/news.tsx
```

### 2. Add to Tab Navigation
Edit: `/app/frontend/app/(tabs)/_layout.tsx`

Add:
```typescript
<Tabs.Screen
  name="news"
  options={{
    title: 'News',
    tabBarIcon: ({ color, size }) => <Ionicons name="newspaper" size={size} color={color} />,
  }}
/>
```

### 3. Restart Frontend
```bash
sudo supervisorctl restart expo
```

---

## 🔐 Security Updates

### Change JWT Secret:
**File:** `/app/backend/.env`
```
JWT_SECRET=your_new_super_secret_key_here_make_it_long_and_random
```

### Update API Keys:
**File:** `/app/backend/.env`
```
TWELVE_DATA_API_KEY=your_new_api_key
TELEGRAM_BOT_TOKEN=your_new_bot_token
EMERGENT_LLM_KEY=your_new_llm_key
```

**Always restart after changing .env:**
```bash
sudo supervisorctl restart backend
```

---

## 📊 Monitor App Performance

### View Backend Logs:
```bash
tail -f /var/log/supervisor/backend.err.log
```

### View Frontend Logs:
```bash
tail -f /var/log/supervisor/expo.out.log
```

### Check for Errors:
```bash
# Backend errors
grep ERROR /var/log/supervisor/backend.err.log | tail -20

# Frontend errors
grep ERROR /var/log/supervisor/expo.err.log | tail -20
```

---

## 🐛 Troubleshooting

### App Not Loading:
1. Check services are running:
```bash
sudo supervisorctl status
```

2. Restart both services:
```bash
sudo supervisorctl restart backend expo
```

### Changes Not Showing:
1. Clear cache and restart:
```bash
sudo supervisorctl restart expo
```

2. Hard refresh browser (Ctrl+F5 or Cmd+Shift+R)

### Database Issues:
1. Check MongoDB is running:
```bash
sudo supervisorctl status
```

2. Reset database with fresh data:
```bash
cd /app/backend
python seed_demo_signals.py
```

### API Key Errors:
1. Verify API key in `/app/backend/.env`
2. Check logs:
```bash
grep "Error fetching price data" /var/log/supervisor/backend.err.log | tail -5
```
3. Test API key at: https://twelvedata.com/docs

---

## 💡 Quick Tips

1. **Always test locally** before making major changes
2. **Keep backups** of working code before big updates
3. **Check logs** after every change
4. **Restart services** after editing config files
5. **Test on mobile** using the QR code from expo

---

## 📱 Mobile Testing

### Get QR Code:
The QR code is displayed in the expo logs:
```bash
tail -f /var/log/supervisor/expo.out.log
```

Look for: "Tunnel ready" message with QR code

### Test on Phone:
1. Download "Expo Go" app (iOS/Android)
2. Scan QR code
3. App will load on your device

---

## 🎨 Customization Ideas

1. **Add push notifications** for new signals
2. **Create signal templates** for quick posting
3. **Add signal voting** system (users vote on accuracy)
4. **Implement referral system** for premium upgrades
5. **Add educational content** section
6. **Create signal alerts** with custom conditions
7. **Add portfolio tracking**
8. **Integrate payment gateway** (Stripe, PayPal)

---

## 📞 Need Help?

1. Check `/app/README_FEATURES.md` for complete feature docs
2. Review code comments in files
3. Check error logs for specific issues
4. Test changes incrementally

---

## ✅ Update Checklist

Before making updates:
- [ ] Backup current working code
- [ ] Read relevant documentation
- [ ] Test in development first
- [ ] Check logs after changes
- [ ] Restart appropriate services
- [ ] Test on mobile device
- [ ] Verify database changes

---

**Last Updated:** February 2026
**App Version:** 1.0.0
**Win Rate:** 98.0% 🏆

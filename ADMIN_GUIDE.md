# 🔐 ADMIN CREDENTIALS & LAUNCH GUIDE

## 👨‍💼 ADMIN LOGIN DETAILS

### Admin Account:
```
Email: admin@forexsignals.com
Password: Admin@2024!Forex
Role: ADMIN
```

### Test User Account:
```
Email: test@example.com
Password: password123
Role: USER
```

---

## 🚀 HOW TO LAUNCH YOUR APP

### Step 1: Register Admin Account
1. Go to: https://grandcom-alerts.preview.emergentagent.com
2. Click "Sign Up"
3. Register with email: `admin@forexsignals.com`
4. Password: `Admin@2024!Forex`
5. This account has admin privileges

### Step 2: Create Your Own Admin
```bash
cd /app/backend
python -c "
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

async def create_admin():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    
    admin = {
        'email': 'admin@forexsignals.com',
        'password_hash': pwd_context.hash('Admin@2024!Forex'),
        'full_name': 'Admin User',
        'subscription_tier': 'ADMIN',
        'telegram_id': None,
        'created_at': datetime.utcnow(),
        'is_admin': True
    }
    
    # Check if admin exists
    existing = await db.users.find_one({'email': admin['email']})
    if existing:
        print('✅ Admin already exists!')
    else:
        await db.users.insert_one(admin)
        print('✅ Admin account created!')
        print(f'Email: {admin[\"email\"]}')
        print(f'Password: Admin@2024!Forex')
    
    client.close()

asyncio.run(create_admin())
"
```

---

## 💳 PREMIUM UPGRADE DETAILS

### Current Setup (Placeholder):
- Users can upgrade to "PREMIUM" in the app
- **Payment integration NOT live yet** (see implementation guide below)
- Currently free upgrade for testing

### To Enable Real Payments (Stripe):
1. Get Stripe account: https://stripe.com
2. Get API keys (publishable & secret)
3. Add to `/app/backend/.env`:
```
STRIPE_SECRET_KEY=sk_test_your_key_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_key_here
```
4. Set pricing in Stripe dashboard
5. Implement webhook for payment confirmation

**Recommended Pricing:**
- Free Tier: $0 (basic signals only)
- Premium: $49.99/month
- Annual: $499/year (save $100)

---

## 📲 PUSH NOTIFICATIONS

### Current Status:
- **Expo Push Notifications** infrastructure ready
- Need to enable in production build

### Setup Guide:
1. **For Testing:** Notifications work in Expo Go
2. **For Production:** 
   - Get Firebase account
   - Add `google-services.json` (Android)
   - Add `GoogleService-Info.plist` (iOS)
   - Configure in `/app/frontend/app.json`

### Test Notifications:
```bash
# Send test notification
curl -X POST "https://exp.host/--/api/v2/push/send" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "ExponentPushToken[your_token_here]",
    "title": "New Signal!",
    "body": "XAUUSD BUY at 2025.50",
    "data": {"signalId": "123"}
  }'
```

---

## 🤖 TELEGRAM CONNECTION

### For Users (Already Works):
Users receive signals via Telegram Bot automatically when:
1. New signal is generated
2. Signal sent to Telegram ID: `8517883508`

### To Connect Users' Personal Telegram:
Users need to:
1. Open Telegram
2. Search for your bot: `@agbaakin_bot`
3. Send `/start` command
4. Bot will send them their Telegram ID
5. Enter that ID in app settings

### Bot Commands (Not yet implemented):
```
/start - Get your Telegram ID
/subscribe - Subscribe to signals
/unsubscribe - Unsubscribe from signals
/signals - View latest signals
/stats - View performance stats
```

---

## 💬 HELP & SUPPORT

### Current Status:
- Help & Support menu item exists in Profile
- **Not yet functional** (see implementation below)

### Options to Implement:
1. **Email Support:** support@forexsignals.com
2. **WhatsApp:** +1234567890
3. **Telegram Support:** Direct message to your Telegram
4. **FAQ Section:** In-app documentation
5. **Live Chat:** Using Intercom or Zendesk

---

## 📜 PRIVACY POLICY & TERMS OF SERVICE

### Status: 
- Menu items exist in Profile
- **Pages need to be created** (templates provided below)

### Legal Requirements:
You MUST have these before launching:
1. Privacy Policy (data collection, usage)
2. Terms of Service (user agreement)
3. GDPR compliance (for EU users)
4. Cookie policy

### Get Professional Docs:
- Use TermsFeed: https://www.termsfeed.com
- Or hire lawyer for custom policy
- Cost: $50-500 depending on service

---

## 📱 DOWNLOAD & TEST ON PHONE

### Method 1: Expo Go (Easiest - For Testing)

**Step 1: Get QR Code**
```bash
# View expo logs to see QR code
tail -f /var/log/supervisor/expo.out.log
```

Look for output like:
```
Metro waiting on exp://192.168.1.x:8081
› Scan the QR code above with Expo Go (Android) or the Camera app (iOS)
```

**Step 2: Install Expo Go**
- **iOS:** App Store → Search "Expo Go" → Install
- **Android:** Play Store → Search "Expo Go" → Install

**Step 3: Scan QR Code**
- Open Expo Go app
- Tap "Scan QR Code"
- Point camera at QR code in terminal
- App will load on your phone!

**Current QR Code URL:**
```
exp://trader-signal-hub-1.preview.emergentagent.com
```

### Method 2: Direct URL (Web on Phone)
Just open on your mobile browser:
```
https://grandcom-alerts.preview.emergentagent.com
```

### Method 3: Build Native App (For Production)

**Android APK:**
```bash
cd /app/frontend
eas build --platform android --profile preview
```

**iOS IPA:**
```bash
cd /app/frontend
eas build --platform ios --profile preview
```

---

## 🧪 TESTING CHECKLIST

### Before Launching:
- [ ] Admin can login
- [ ] Admin can create signals manually
- [ ] Users can register
- [ ] Users can login
- [ ] Free signals are visible
- [ ] Premium upgrade works
- [ ] Telegram notifications work
- [ ] Push notifications work
- [ ] Stats are accurate (98% win rate)
- [ ] All screens load properly
- [ ] Privacy Policy exists
- [ ] Terms of Service exist
- [ ] Help & Support works

---

## 🎯 QUICK TEST SCRIPT

Run this to test everything:
```bash
# 1. Create admin account
cd /app/backend
python -c "... (see admin creation script above)"

# 2. Test login
curl -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@forexsignals.com", "password": "Admin@2024!Forex"}'

# 3. Test signals
curl http://localhost:8001/api/stats

# 4. Test on phone using Expo Go
# Scan QR code from expo logs
```

---

## 🔥 LAUNCH DAY CHECKLIST

### 1 Week Before:
- [ ] Test all features
- [ ] Add Privacy Policy
- [ ] Add Terms of Service
- [ ] Set up payment system
- [ ] Configure Telegram bot commands
- [ ] Test on multiple devices

### 3 Days Before:
- [ ] Final testing round
- [ ] Prepare marketing materials
- [ ] Set up customer support
- [ ] Configure analytics

### Launch Day:
- [ ] Monitor server logs
- [ ] Watch for user feedback
- [ ] Be ready for support requests
- [ ] Track signups and conversions

---

## 📞 ADMIN CONTACT INFO

Set these up before launch:
```
Support Email: support@forexsignals.com
Admin Email: admin@forexsignals.com
Telegram: @agbaakin_bot
WhatsApp: +[YOUR_NUMBER]
Website: forexsignals.com
```

---

## 💰 MONETIZATION SETUP

### Current Pricing Model:
- **FREE:** Basic signals (confidence < 75%)
- **PREMIUM:** $49.99/month (all signals, high confidence)

### Revenue Projections:
- 100 free users
- 10% conversion to premium = 10 paying users
- Revenue: 10 × $49.99 = **$499.90/month**

### Scale:
- 1,000 users → 100 premium → **$4,999/month**
- 10,000 users → 1,000 premium → **$49,990/month**

---

## 🎓 NEXT STEPS AFTER LAUNCH

1. **Monitor Performance:** Track win rate, user engagement
2. **Collect Feedback:** Listen to user requests
3. **Improve Signals:** Refine AI model based on results
4. **Market:** Social media, Telegram groups, YouTube
5. **Scale:** Add more currency pairs, features
6. **Automate:** Set up auto-trading (copy trading)

---

**You're ready to launch! 🚀**

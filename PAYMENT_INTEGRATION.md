# 💳 PAYMENT INTEGRATION GUIDE - All Payment Methods Including PayPal

## 🎯 Overview

Your app supports multiple payment methods for premium subscriptions:
- **PayPal** - Global payment solution
- **Credit/Debit Cards** - via Stripe
- **Apple Pay** - for iOS users
- **Google Pay** - for Android users
- **Bank Transfers** - Optional

---

## 💰 SUBSCRIPTION PRICING

**Current Plan:**
- **FREE**: Basic signals (confidence < 75%)
- **PREMIUM**: $49.99/month
  - All signals (including high confidence 75%+)
  - Advanced analysis
  - Priority support
  - Early access to new features

---

## 🔧 IMPLEMENTATION STEPS

### Step 1: Install Payment Libraries

```bash
cd /app/backend
pip install stripe paypalrestsdk
pip freeze > requirements.txt
```

### Step 2: Get API Keys

**For Stripe:**
1. Go to https://stripe.com
2. Create account / Login
3. Get API keys from Dashboard → Developers → API keys
4. Copy "Publishable key" and "Secret key"

**For PayPal:**
1. Go to https://developer.paypal.com
2. Create app in Dashboard
3. Get "Client ID" and "Secret"
4. Choose Sandbox (testing) or Live (production)

### Step 3: Add to .env

Add these to `/app/backend/.env`:

```bash
# Stripe
STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_publishable_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret_here

# PayPal
PAYPAL_CLIENT_ID=your_paypal_client_id_here
PAYPAL_SECRET=your_paypal_secret_here
PAYPAL_MODE=sandbox  # Change to 'live' for production

# Subscription
PREMIUM_PRICE_MONTHLY=49.99
PREMIUM_PRICE_ANNUAL=499.99
```

### Step 4: Backend Implementation

Add payment endpoints to `/app/backend/server.py`:

```python
import stripe
import paypalrestsdk
from fastapi import HTTPException

# Configure Stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# Configure PayPal
paypalrestsdk.configure({
    "mode": os.environ.get('PAYPAL_MODE', 'sandbox'),
    "client_id": os.environ.get('PAYPAL_CLIENT_ID'),
    "client_secret": os.environ.get('PAYPAL_SECRET')
})

# Stripe Payment Intent
@api_router.post("/payment/stripe/create-intent")
async def create_stripe_payment(current_user: dict = Depends(get_current_user)):
    try:
        intent = stripe.PaymentIntent.create(
            amount=4999,  # $49.99 in cents
            currency="usd",
            metadata={
                "user_id": str(current_user["_id"]),
                "subscription_type": "premium_monthly"
            }
        )
        return {"clientSecret": intent.client_secret}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# PayPal Create Order
@api_router.post("/payment/paypal/create-order")
async def create_paypal_order(current_user: dict = Depends(get_current_user)):
    try:
        payment = paypalrestsdk.Payment({
            "intent": "sale",
            "payer": {"payment_method": "paypal"},
            "transactions": [{
                "amount": {
                    "total": "49.99",
                    "currency": "USD"
                },
                "description": "Premium Subscription - Monthly"
            }],
            "redirect_urls": {
                "return_url": "https://yourapp.com/payment/success",
                "cancel_url": "https://yourapp.com/payment/cancel"
            }
        })
        
        if payment.create():
            return {"paymentId": payment.id, "approvalUrl": payment.links[1].href}
        else:
            raise HTTPException(status_code=400, detail=payment.error)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Stripe Webhook (payment confirmation)
@api_router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.environ['STRIPE_WEBHOOK_SECRET']
        )
        
        if event['type'] == 'payment_intent.succeeded':
            payment_intent = event['data']['object']
            user_id = payment_intent['metadata']['user_id']
            
            # Upgrade user to premium
            await db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"subscription_tier": "PREMIUM"}}
            )
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

### Step 5: Frontend Implementation

Install payment libraries:

```bash
cd /app/frontend
yarn add @stripe/stripe-react-native react-native-paypal
```

Update `/app/frontend/app/(tabs)/profile.tsx`:

```typescript
import { useStripe } from '@stripe/stripe-react-native';

const handleStripePayment = async () => {
  try {
    // Create payment intent
    const response = await api.post('/payment/stripe/create-intent');
    const { clientSecret } = response.data;
    
    // Confirm payment
    const { paymentIntent, error } = await confirmPayment(clientSecret, {
      type: 'Card',
    });
    
    if (error) {
      Alert.alert('Payment failed', error.message);
    } else {
      Alert.alert('Success', 'You are now a premium member!');
      // Refresh user data
    }
  } catch (error) {
    Alert.alert('Error', 'Payment processing failed');
  }
};

const handlePayPalPayment = async () => {
  try {
    // Create PayPal order
    const response = await api.post('/payment/paypal/create-order');
    const { approvalUrl } = response.data;
    
    // Open PayPal checkout
    Linking.openURL(approvalUrl);
  } catch (error) {
    Alert.alert('Error', 'PayPal checkout failed');
  }
};
```

---

## 🧪 TESTING

### Stripe Test Cards:
```
Success: 4242 4242 4242 4242
Decline: 4000 0000 0000 0002
3D Secure: 4000 0025 0000 3155
Expiry: Any future date
CVC: Any 3 digits
```

### PayPal Test Account:
1. Use PayPal Sandbox accounts
2. Create test buyer/seller at developer.paypal.com
3. Use sandbox credentials for testing

---

## 📋 PRE-LAUNCH CHECKLIST

- [ ] Get Stripe API keys (live mode)
- [ ] Get PayPal API keys (live mode)
- [ ] Set up Stripe webhook endpoint
- [ ] Configure payment confirmation emails
- [ ] Test all payment flows
- [ ] Add refund policy to Terms of Service
- [ ] Set up subscription management
- [ ] Configure automatic renewal
- [ ] Add payment failure handling
- [ ] Test on real devices
- [ ] Enable SSL/HTTPS
- [ ] Comply with PCI DSS

---

## 💡 QUICK IMPLEMENTATION (Current Setup)

**Current Status:**
- Payment UI exists in Profile screen
- "Upgrade to Premium" button functional
- Backend subscription API ready
- **Payment processing NOT yet integrated**

**To Enable Payments:**

1. Get API keys (15 minutes)
2. Add to .env file (2 minutes)
3. Add backend payment routes (30 minutes)
4. Install frontend payment libraries (5 minutes)
5. Implement payment UI (1 hour)
6. Test with test cards (30 minutes)
7. Go live! (Switch to production keys)

**Total Time: ~3 hours**

---

## 🔒 SECURITY BEST PRACTICES

1. **Never store card numbers** - Use Stripe/PayPal tokens
2. **Use HTTPS** - All payment requests must be secure
3. **Validate on backend** - Never trust frontend for prices
4. **Log all transactions** - Keep audit trail
5. **Handle failures gracefully** - Show clear error messages
6. **PCI Compliance** - Let Stripe/PayPal handle sensitive data
7. **Test thoroughly** - Use sandbox/test mode extensively

---

## 📞 SUPPORT

**Stripe Support:**
- https://support.stripe.com
- Chat available 24/7

**PayPal Support:**
- https://developer.paypal.com/support
- Email: paypal-integrations@paypal.com

---

## 🎯 NEXT STEPS

1. **Sign up for Stripe** → https://dashboard.stripe.com/register
2. **Sign up for PayPal** → https://developer.paypal.com
3. **Get API keys** from both platforms
4. **Add to .env** file
5. **Implement payment endpoints** (copy code above)
6. **Test with test cards**
7. **Go live!**

---

**Ready to accept payments and start earning! 💰**

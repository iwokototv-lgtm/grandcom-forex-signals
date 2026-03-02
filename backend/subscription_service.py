"""
Subscription Service for Grandcom Forex Signals Pro
Handles subscription tiers, payments via Stripe, and feature gating
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass
from bson import ObjectId

logger = logging.getLogger(__name__)

# ============ SUBSCRIPTION TIERS ============
class SubscriptionTier(Enum):
    FREE = "free"
    PRO = "pro"
    PREMIUM = "premium"

# Define subscription packages with fixed prices (NEVER accept from frontend)
SUBSCRIPTION_PACKAGES = {
    "pro_monthly": {
        "tier": SubscriptionTier.PRO.value,
        "name": "Pro Monthly",
        "price": 29.99,
        "currency": "usd",
        "duration_days": 30,
        "features": [
            "All trading pairs",
            "Real-time signals",
            "Push notifications",
            "Basic analytics",
            "Email support"
        ]
    },
    "pro_yearly": {
        "tier": SubscriptionTier.PRO.value,
        "name": "Pro Yearly",
        "price": 299.99,
        "currency": "usd",
        "duration_days": 365,
        "features": [
            "All trading pairs",
            "Real-time signals", 
            "Push notifications",
            "Basic analytics",
            "Email support",
            "2 months FREE"
        ]
    },
    "premium_monthly": {
        "tier": SubscriptionTier.PREMIUM.value,
        "name": "Premium Monthly",
        "price": 79.99,
        "currency": "usd",
        "duration_days": 30,
        "features": [
            "All Pro features",
            "Advanced ML analytics",
            "Historical backtesting",
            "Custom TP/SL settings",
            "Priority signals",
            "24/7 priority support",
            "Trade copier integration"
        ]
    },
    "premium_yearly": {
        "tier": SubscriptionTier.PREMIUM.value,
        "name": "Premium Yearly",
        "price": 799.99,
        "currency": "usd",
        "duration_days": 365,
        "features": [
            "All Pro features",
            "Advanced ML analytics",
            "Historical backtesting",
            "Custom TP/SL settings",
            "Priority signals",
            "24/7 priority support",
            "Trade copier integration",
            "2 months FREE"
        ]
    }
}

# Feature access by tier
TIER_FEATURES = {
    SubscriptionTier.FREE.value: {
        "max_signals_per_day": 3,
        "pairs": ["EURUSD", "GBPUSD", "XAUUSD"],
        "push_notifications": False,
        "analytics": False,
        "backtesting": False,
        "priority_signals": False
    },
    SubscriptionTier.PRO.value: {
        "max_signals_per_day": 50,
        "pairs": "all",
        "push_notifications": True,
        "analytics": True,
        "backtesting": False,
        "priority_signals": False
    },
    SubscriptionTier.PREMIUM.value: {
        "max_signals_per_day": "unlimited",
        "pairs": "all",
        "push_notifications": True,
        "analytics": True,
        "backtesting": True,
        "priority_signals": True
    }
}


class SubscriptionService:
    """Handles subscription management and payment processing"""
    
    def __init__(self, db, stripe_api_key: str):
        self.db = db
        self.stripe_api_key = stripe_api_key
    
    async def get_user_subscription(self, user_id: str) -> Dict[str, Any]:
        """Get user's current subscription status"""
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"tier": "free", "features": TIER_FEATURES["free"]}
            
            subscription = await self.db.subscriptions.find_one({
                "user_id": user_id,
                "status": "active",
                "expires_at": {"$gt": datetime.now(timezone.utc)}
            })
            
            if subscription:
                tier = subscription.get("tier", "free")
                return {
                    "tier": tier,
                    "expires_at": subscription.get("expires_at").isoformat(),
                    "package": subscription.get("package_id"),
                    "features": TIER_FEATURES.get(tier, TIER_FEATURES["free"])
                }
            
            return {
                "tier": "free",
                "features": TIER_FEATURES["free"]
            }
            
        except Exception as e:
            logger.error(f"Error getting subscription: {e}")
            return {"tier": "free", "features": TIER_FEATURES["free"]}
    
    async def check_feature_access(self, user_id: str, feature: str) -> bool:
        """Check if user has access to a specific feature"""
        subscription = await self.get_user_subscription(user_id)
        features = subscription.get("features", {})
        return features.get(feature, False)
    
    async def create_checkout_session(
        self,
        user_id: str,
        package_id: str,
        origin_url: str
    ) -> Dict[str, Any]:
        """Create a Stripe checkout session for subscription"""
        try:
            from emergentintegrations.payments.stripe.checkout import (
                StripeCheckout, CheckoutSessionRequest
            )
            
            # Validate package exists (security: never accept price from frontend)
            if package_id not in SUBSCRIPTION_PACKAGES:
                return {"success": False, "error": "Invalid package"}
            
            package = SUBSCRIPTION_PACKAGES[package_id]
            
            # Build URLs from provided origin
            success_url = f"{origin_url}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}"
            cancel_url = f"{origin_url}/subscription"
            
            # Initialize Stripe
            webhook_url = f"{origin_url}/api/webhook/stripe"
            stripe_checkout = StripeCheckout(
                api_key=self.stripe_api_key,
                webhook_url=webhook_url
            )
            
            # Create checkout request
            checkout_request = CheckoutSessionRequest(
                amount=float(package["price"]),
                currency=package["currency"],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "user_id": user_id,
                    "package_id": package_id,
                    "tier": package["tier"],
                    "duration_days": str(package["duration_days"])
                }
            )
            
            # Create session
            session = await stripe_checkout.create_checkout_session(checkout_request)
            
            # Record transaction as pending
            await self.db.payment_transactions.insert_one({
                "user_id": user_id,
                "session_id": session.session_id,
                "package_id": package_id,
                "amount": package["price"],
                "currency": package["currency"],
                "status": "pending",
                "payment_status": "initiated",
                "created_at": datetime.now(timezone.utc)
            })
            
            return {
                "success": True,
                "checkout_url": session.url,
                "session_id": session.session_id
            }
            
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            return {"success": False, "error": str(e)}
    
    async def verify_payment(self, session_id: str) -> Dict[str, Any]:
        """Verify payment status and activate subscription"""
        try:
            from emergentintegrations.payments.stripe.checkout import StripeCheckout
            
            # Check if already processed
            existing = await self.db.payment_transactions.find_one({
                "session_id": session_id,
                "payment_status": "paid"
            })
            
            if existing:
                return {
                    "success": True,
                    "status": "already_processed",
                    "message": "Payment already processed"
                }
            
            # Get transaction record
            transaction = await self.db.payment_transactions.find_one({
                "session_id": session_id
            })
            
            if not transaction:
                return {"success": False, "error": "Transaction not found"}
            
            # Check payment status with Stripe
            stripe_checkout = StripeCheckout(api_key=self.stripe_api_key)
            status = await stripe_checkout.get_checkout_status(session_id)
            
            # Update transaction
            await self.db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "status": status.status,
                    "payment_status": status.payment_status,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            if status.payment_status == "paid":
                # Activate subscription
                package = SUBSCRIPTION_PACKAGES.get(transaction["package_id"])
                if package:
                    expires_at = datetime.now(timezone.utc) + timedelta(days=package["duration_days"])
                    
                    # Deactivate existing subscription
                    await self.db.subscriptions.update_many(
                        {"user_id": transaction["user_id"], "status": "active"},
                        {"$set": {"status": "superseded"}}
                    )
                    
                    # Create new subscription
                    await self.db.subscriptions.insert_one({
                        "user_id": transaction["user_id"],
                        "package_id": transaction["package_id"],
                        "tier": package["tier"],
                        "status": "active",
                        "starts_at": datetime.now(timezone.utc),
                        "expires_at": expires_at,
                        "payment_session_id": session_id,
                        "created_at": datetime.now(timezone.utc)
                    })
                    
                    # Update user's subscription tier
                    await self.db.users.update_one(
                        {"_id": ObjectId(transaction["user_id"])},
                        {"$set": {"subscription_tier": package["tier"]}}
                    )
                    
                    return {
                        "success": True,
                        "status": "paid",
                        "tier": package["tier"],
                        "expires_at": expires_at.isoformat()
                    }
            
            return {
                "success": True,
                "status": status.status,
                "payment_status": status.payment_status
            }
            
        except Exception as e:
            logger.error(f"Error verifying payment: {e}")
            return {"success": False, "error": str(e)}
    
    async def cancel_subscription(self, user_id: str) -> Dict[str, Any]:
        """Cancel user's subscription (won't renew)"""
        try:
            result = await self.db.subscriptions.update_one(
                {"user_id": user_id, "status": "active"},
                {"$set": {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc)}}
            )
            
            if result.modified_count > 0:
                return {"success": True, "message": "Subscription cancelled"}
            return {"success": False, "error": "No active subscription found"}
            
        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}")
            return {"success": False, "error": str(e)}


# Global instance
subscription_service: Optional[SubscriptionService] = None


def init_subscription_service(db, stripe_api_key: str) -> SubscriptionService:
    """Initialize the subscription service"""
    global subscription_service
    subscription_service = SubscriptionService(db, stripe_api_key)
    return subscription_service


def get_subscription_service() -> Optional[SubscriptionService]:
    """Get the subscription service instance"""
    return subscription_service

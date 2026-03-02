"""
Push Notification Service for Grandcom Forex Signals Pro
Uses Expo Push Notifications to send alerts to mobile users
"""

import asyncio
import logging
import aiohttp
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


class PushNotificationService:
    """Service for sending push notifications via Expo"""
    
    def __init__(self, db):
        self.db = db
    
    async def register_push_token(self, user_id: str, push_token: str, device_type: str = "unknown") -> bool:
        """Register a user's push notification token"""
        try:
            # Validate Expo push token format
            if not push_token.startswith("ExponentPushToken[") and not push_token.startswith("ExpoPushToken["):
                logger.warning(f"Invalid push token format: {push_token[:20]}...")
                return False
            
            # Upsert the token for this user
            await self.db.push_tokens.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "push_token": push_token,
                        "device_type": device_type,
                        "updated_at": datetime.now(timezone.utc),
                        "is_active": True
                    },
                    "$setOnInsert": {
                        "created_at": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            
            logger.info(f"Push token registered for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error registering push token: {e}")
            return False
    
    async def unregister_push_token(self, user_id: str) -> bool:
        """Unregister/deactivate a user's push token"""
        try:
            await self.db.push_tokens.update_one(
                {"user_id": user_id},
                {"$set": {"is_active": False}}
            )
            return True
        except Exception as e:
            logger.error(f"Error unregistering push token: {e}")
            return False
    
    async def get_active_tokens(self) -> List[str]:
        """Get all active push tokens"""
        try:
            tokens = await self.db.push_tokens.find(
                {"is_active": True}
            ).to_list(length=10000)
            
            return [t["push_token"] for t in tokens if t.get("push_token")]
        except Exception as e:
            logger.error(f"Error getting active tokens: {e}")
            return []
    
    async def send_notification(
        self,
        push_tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        sound: str = "default",
        badge: Optional[int] = None,
        channel_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Send push notifications to multiple devices
        
        Args:
            push_tokens: List of Expo push tokens
            title: Notification title
            body: Notification body text
            data: Optional custom data payload
            sound: Sound to play (default, or custom sound file)
            badge: Badge count for iOS
            channel_id: Android notification channel
        
        Returns:
            Dict with success count and any errors
        """
        if not push_tokens:
            return {"success": 0, "errors": [], "message": "No tokens provided"}
        
        results = {
            "success": 0,
            "failed": 0,
            "errors": []
        }
        
        # Expo accepts up to 100 messages per request
        # Split into chunks of 100
        chunk_size = 100
        for i in range(0, len(push_tokens), chunk_size):
            chunk = push_tokens[i:i + chunk_size]
            
            messages = [
                {
                    "to": token,
                    "title": title,
                    "body": body,
                    "sound": sound,
                    "channelId": channel_id,
                    "priority": "high",
                    **({"data": data} if data else {}),
                    **({"badge": badge} if badge is not None else {})
                }
                for token in chunk
            ]
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        EXPO_PUSH_URL,
                        json=messages,
                        headers={
                            "Accept": "application/json",
                            "Accept-Encoding": "gzip, deflate",
                            "Content-Type": "application/json"
                        },
                        timeout=30
                    ) as response:
                        if response.status == 200:
                            resp_data = await response.json()
                            
                            # Check individual results
                            for idx, ticket in enumerate(resp_data.get("data", [])):
                                if ticket.get("status") == "ok":
                                    results["success"] += 1
                                else:
                                    results["failed"] += 1
                                    error_msg = ticket.get("message", "Unknown error")
                                    results["errors"].append({
                                        "token": chunk[idx][:30] + "...",
                                        "error": error_msg
                                    })
                                    
                                    # Mark invalid tokens as inactive
                                    if "DeviceNotRegistered" in error_msg:
                                        await self.db.push_tokens.update_one(
                                            {"push_token": chunk[idx]},
                                            {"$set": {"is_active": False}}
                                        )
                        else:
                            error_text = await response.text()
                            results["failed"] += len(chunk)
                            results["errors"].append({
                                "error": f"HTTP {response.status}: {error_text[:100]}"
                            })
                            
            except asyncio.TimeoutError:
                results["failed"] += len(chunk)
                results["errors"].append({"error": "Request timeout"})
            except Exception as e:
                results["failed"] += len(chunk)
                results["errors"].append({"error": str(e)})
        
        logger.info(f"Push notifications sent: {results['success']} success, {results['failed']} failed")
        return results
    
    async def send_new_signal_notification(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Send notification for a new trading signal"""
        tokens = await self.get_active_tokens()
        
        if not tokens:
            logger.info("No active push tokens to notify")
            return {"success": 0, "message": "No active subscribers"}
        
        signal_type = signal.get("type", "SIGNAL")
        pair = signal.get("pair", "Unknown")
        entry = signal.get("entry_price", "N/A")
        confidence = signal.get("confidence", 0)
        
        emoji = "🟢" if signal_type == "BUY" else "🔴"
        
        title = f"{emoji} New Signal: {pair} {signal_type}"
        body = f"Entry: {entry} | Confidence: {confidence}%\nTap to view full details"
        
        data = {
            "type": "new_signal",
            "signal_id": signal.get("id", ""),
            "pair": pair,
            "signal_type": signal_type
        }
        
        return await self.send_notification(
            push_tokens=tokens,
            title=title,
            body=body,
            data=data,
            channel_id="signals"
        )
    
    async def send_trade_closed_notification(
        self, 
        signal: Dict[str, Any],
        outcome: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send notification when a trade is closed"""
        tokens = await self.get_active_tokens()
        
        if not tokens:
            return {"success": 0, "message": "No active subscribers"}
        
        pair = signal.get("pair", "Unknown")
        result = outcome.get("result", "UNKNOWN")
        pips = outcome.get("pips", 0)
        
        emoji = "✅" if result == "WIN" else "❌"
        pips_text = f"+{pips:.1f}" if pips > 0 else f"{pips:.1f}"
        
        title = f"{emoji} Trade Closed: {pair}"
        body = f"Result: {result} | Pips: {pips_text}\nTap to view performance"
        
        data = {
            "type": "trade_closed",
            "pair": pair,
            "result": result,
            "pips": pips
        }
        
        return await self.send_notification(
            push_tokens=tokens,
            title=title,
            body=body,
            data=data,
            channel_id="signals"
        )


# Global instance
push_service: Optional[PushNotificationService] = None


def init_push_service(db) -> PushNotificationService:
    """Initialize the global push notification service"""
    global push_service
    push_service = PushNotificationService(db)
    return push_service


def get_push_service() -> Optional[PushNotificationService]:
    """Get the global push notification service"""
    return push_service

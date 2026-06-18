"""
Telegram notification service.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramService:
    """Sends notifications to Telegram."""

    def __init__(self, bot, channel_id, retry_func):
        self.bot = bot
        self.channel_id = channel_id
        self.retry_func = retry_func

    async def send_signal(
        self,
        pair: str,
        signal_type: str,
        entry: float,
        tps: list,
        sl: float,
        confidence: float,
        rr: float,
        analysis: str,
        regime: str = "UNKNOWN",
        smc_score: int = 0,
        mtf_alignment: float = 0.0,
        position_count: int = 0,
        exposure_pct: float = 0.0,
        risk_status: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send a signal notification to Telegram.

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            emoji = "🟢" if signal_type == "BUY" else "🔴"
            action = signal_type.capitalize()
            lo = round(entry - 0.50, 2)
            hi = round(entry + 0.50, 2)

            copier_msg = (
                f"{emoji} #{pair} [SWING]\n"
                f"\n"
                f"{action} {lo} - {hi}\n"
                f"\n"
                f"TP1: {tps[0]}\n"
                f"TP2: {tps[1]}\n"
                f"TP3: {tps[2]}\n"
                f"\n"
                f"SL: {sl}\n"
            )

            rs = risk_status or {}
            daily_pnl = rs.get("daily_pnl", 0.0)
            daily_loss_pct = rs.get("daily_loss_pct", 0.0)
            drawdown_pct = rs.get("drawdown_pct", 0.0)
            risk_level = rs.get("risk_level", "GREEN")
            risk_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(risk_level, "⚪")

            info_msg = (
                f"<b>📊 R:R:</b> 1:{rr}  "
                f"<b>⚡ Confidence:</b> {confidence}%\n"
                f"<b>🎯 Regime:</b> {regime}  "
                f"<b>📐 SMC:</b> {smc_score}/10  "
                f"<b>🔗 MTF:</b> {mtf_alignment:.0f}%\n"
                f"<b>📈 Positions:</b> {position_count}/5  "
                f"<b>💰 Exposure:</b> {exposure_pct:.1f}%\n"
                f"<b>📉 Daily P&L:</b> ${daily_pnl:+.2f} ({daily_loss_pct:.1f}%)  "
                f"<b>📉 Drawdown:</b> {drawdown_pct:.1f}%\n"
                f"<b>🛡 Risk:</b> {risk_emoji} {risk_level}\n"
                f"<b>📝</b> {_html_escape(analysis)}\n"
                f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
                f"| Grandcom Gold Engine v3.0</i>"
            )

            logger.info(
                f"[{pair}] ✅ STAGE 4 - TELEGRAM SEND START: "
                f"{signal_type} confidence={confidence}%"
            )

            async def _send():
                await self.bot.send_message(
                    chat_id=self.channel_id, text=copier_msg
                )
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=info_msg,
                    parse_mode="HTML",
                )

            result = await self.retry_func(
                f"telegram_send[{pair}]",
                _send,
            )

            if result is not None:
                logger.info(f"[{pair}] ✅ STAGE 4 - TELEGRAM SUCCESS")
                return True
            else:
                logger.error(f"[{pair}] ❌ STAGE 4 - TELEGRAM NOTIFICATION FAILED")
                return False

        except Exception as exc:
            logger.error(f"[{pair}] ❌ STAGE 4 - TELEGRAM ERROR: {exc}")
            return False

    async def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """
        Send a plain message to the configured channel.

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            async def _send():
                kwargs: Dict[str, Any] = {"chat_id": self.channel_id, "text": text}
                if parse_mode:
                    kwargs["parse_mode"] = parse_mode
                await self.bot.send_message(**kwargs)

            result = await self.retry_func("telegram_message", _send)
            return result is not None
        except Exception as exc:
            logger.error(f"[TelegramService] send_message failed: {exc}")
            return False

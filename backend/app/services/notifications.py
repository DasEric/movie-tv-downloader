"""Discord + Telegram webhook notifications."""
from __future__ import annotations

import html
import logging

from app.services import settings_store
from app.services.http import get_client

log = logging.getLogger(__name__)


async def notify(title: str, message: str, success: bool = True) -> None:
    await _discord(title, message, success)
    await _telegram(title, message, success)


async def _discord(title: str, message: str, success: bool) -> None:
    webhook = await settings_store.get("discord_webhook_url")
    if not webhook:
        return
    color = 0x22C55E if success else 0xEF4444
    payload = {
        "embeds": [
            {
                "title": f"H0melab Downloader — {title}",
                "description": message,
                "color": color,
            }
        ]
    }
    try:
        c = await get_client()
        await c.post(webhook, json=payload, timeout=10)
    except Exception as e:
        log.warning("discord notify failed: %s", e)


async def _telegram(title: str, message: str, success: bool) -> None:
    token = await settings_store.get("telegram_bot_token")
    chat_id = await settings_store.get("telegram_chat_id")
    if not token or not chat_id:
        return
    icon = "✅" if success else "❌"
    # Use HTML parse mode with escaped content — avoids Markdown parse
    # errors on titles/messages that contain underscores, asterisks or
    # backticks (common with episode names).
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    text = f"<b>{icon} H0melab Downloader — {safe_title}</b>\n{safe_message}"
    try:
        c = await get_client()
        await c.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning("telegram notify failed: %s", e)

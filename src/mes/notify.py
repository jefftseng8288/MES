"""Telegram push for MES alarms — the only outbound channel.

Credential-gated: if MES_TELEGRAM_BOT_TOKEN / MES_TELEGRAM_CHAT_ID are unset, this is
a logged no-op (the alarm check still runs and records to the DB). Never raises — a
push failure must not break the alarm run.
"""

from __future__ import annotations

import logging

import httpx

from mes.config import get_settings

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str, *, timeout: float = 15.0) -> bool:
    """Send one Telegram message. Returns True on delivery, False otherwise (no raise)."""
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        logger.warning("[notify] Telegram not configured (token/chat_id empty) — skipping push")
        return False
    try:
        resp = httpx.post(
            _API.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        logger.warning("[notify] Telegram push failed: %s", exc)
        return False
    if resp.status_code != 200:
        logger.warning("[notify] Telegram push HTTP %s: %s", resp.status_code, resp.text[:200])
        return False
    return True

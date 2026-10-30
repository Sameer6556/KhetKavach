"""Alert delivery over the Telegram Bot API (free; works on basic phones via the Telegram app)."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(API.format(token=token),
                                     json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
        if resp.status_code != 200:
            log.warning("Telegram send failed: HTTP %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("Telegram send failed: %s", type(exc).__name__)
        return False

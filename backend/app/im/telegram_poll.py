"""Telegram 轮询模式：无公网地址时用 getUpdates 主动拉取消息。"""

import asyncio
import logging

import httpx
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.im.registry import get_adapter
from app.services.pipeline import handle_inbound

log = logging.getLogger(__name__)


async def poll_forever() -> None:
    token = settings.telegram_bot_token
    base = f"https://api.telegram.org/bot{token}"
    offset = 0
    async with httpx.AsyncClient(timeout=40) as client:
        # 轮询与 webhook 互斥，先删除 webhook（保留未读消息）
        await client.post(f"{base}/deleteWebhook")
        log.info("Telegram polling started")
        while True:
            try:
                r = await client.get(f"{base}/getUpdates", params={
                    "offset": offset, "timeout": 30,
                    "allowed_updates": '["message","edited_message"]'})
                r.raise_for_status()
                for update in r.json().get("result", []):
                    offset = update["update_id"] + 1
                    m = get_adapter("telegram").parse_webhook(update)
                    if not m:
                        continue
                    async with SessionLocal() as s:
                        try:
                            await handle_inbound(s, m)
                        except IntegrityError:
                            await s.rollback()
                        except Exception:
                            log.exception("handle telegram update failed")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("telegram polling error: %s", e)
                await asyncio.sleep(5)

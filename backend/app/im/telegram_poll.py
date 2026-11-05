"""Telegram 轮询模式：无公网地址时用 getUpdates 主动拉取消息。"""

import asyncio
import logging

import httpx
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.im.registry import get_adapter
from app.services import lease
from app.services.pipeline import handle_inbound

log = logging.getLogger(__name__)
LEASE_TTL = 90  # 比 getUpdates 的 30 秒长轮询长得多，正常续期不会断

_inflight: set[asyncio.Task] = set()


async def _handle(m) -> None:
    async with SessionLocal() as s:
        try:
            await handle_inbound(s, m)
        except IntegrityError:  # 重复投递（例如换了轮询进程）—— 已处理过，忽略
            await s.rollback()
        except Exception:
            log.exception("handle telegram update failed")


async def poll_forever() -> None:
    token = settings.telegram_bot_token
    base = f"https://api.telegram.org/bot{token}"
    offset = 0
    async with httpx.AsyncClient(timeout=40) as client:
        # 轮询与 webhook 互斥，先删除 webhook（保留未读消息）
        await client.post(f"{base}/deleteWebhook")
        log.info("Telegram polling started")
        polling = False
        while True:
            # 同一个 bot 只能有一个 getUpdates 连接，多进程时靠租约选出一个
            if not await lease.acquire("telegram_poll", LEASE_TTL):
                if polling:
                    log.info("Telegram polling handed over to another process")
                polling = False
                await asyncio.sleep(10)
                continue
            polling = True
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
                    # 每条消息独立处理，一个群排查十几秒不会卡住其他群；
                    # 同群顺序由 handle_inbound 内的锁保证（先到先得）。
                    task = asyncio.create_task(_handle(m))
                    _inflight.add(task)
                    task.add_done_callback(_inflight.discard)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("telegram polling error: %s", e)
                await asyncio.sleep(5)

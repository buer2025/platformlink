"""跨进程租约锁。

多进程 / 多副本部署时，进程内的 asyncio.Lock 和 "每个进程各跑一份调度器" 都不再安全：
同一条预发消息可能被两个调度器同时发、同一个群的两条消息可能被两个进程并行排查。
这里用数据库里的一行记录当租约：谁写进了自己的 holder 且没过期，谁就持有。
持有者崩溃后租约到期自动释放，不需要人工清理。SQLite 与 PostgreSQL 通用。
"""

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import SessionLocal
from app.models import Lease

log = logging.getLogger(__name__)

# 每个进程一个唯一身份；同一进程重复获取视为续期
HOLDER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _now() -> datetime:
    return datetime.now(UTC)


async def acquire(name: str, ttl: float) -> bool:
    """尝试获取（或续期）租约，成功返回 True。从不阻塞等待。"""
    now = _now()
    expires = now + timedelta(seconds=ttl)
    async with SessionLocal() as s:
        try:
            r = await s.execute(
                update(Lease)
                .where(Lease.name == name, or_(Lease.holder == HOLDER, Lease.expires_at < now))
                .values(holder=HOLDER, expires_at=expires)
            )
            if r.rowcount == 1:
                await s.commit()
                return True
            s.add(Lease(name=name, holder=HOLDER, expires_at=expires))
            await s.commit()
            return True
        except IntegrityError:  # 别人持有且未过期
            await s.rollback()
            return False
        except OperationalError as e:  # SQLite 写锁繁忙等，视为本轮没拿到
            await s.rollback()
            log.debug("lease %s busy: %s", name, e)
            return False


async def release(name: str) -> None:
    async with SessionLocal() as s:
        try:
            await s.execute(delete(Lease).where(Lease.name == name, Lease.holder == HOLDER))
            await s.commit()
        except OperationalError:  # 释放失败也无妨，租约会自然过期
            await s.rollback()


@contextlib.asynccontextmanager
async def hold(name: str, ttl: float = 120, poll: float = 0.3) -> AsyncIterator[None]:
    """阻塞直到拿到租约；持有期间后台定时续期，退出时释放。"""
    while not await acquire(name, ttl):
        await asyncio.sleep(poll)

    async def renew() -> None:
        while True:
            await asyncio.sleep(ttl / 3)
            if not await acquire(name, ttl):
                log.warning("lease %s renewal failed", name)

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await release(name)

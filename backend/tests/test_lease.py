"""多进程安全：租约锁、调度器选主、卡住消息的恢复。"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Lease, Outbound, OutboundStatus
from app.services import lease, presend
from tests.test_flow import client, fake, setup_groups  # noqa: F401


def as_other(monkeypatch, name="other-host:1:abc"):
    monkeypatch.setattr(lease, "HOLDER", name)


async def test_lease_is_exclusive_and_renewable(client, monkeypatch):  # noqa: F811
    assert await lease.acquire("job", 30)
    assert await lease.acquire("job", 30)  # 同一进程再次获取 = 续期
    me = lease.HOLDER
    as_other(monkeypatch)
    assert not await lease.acquire("job", 30)  # 另一个进程拿不到
    monkeypatch.setattr(lease, "HOLDER", me)
    await lease.release("job")
    as_other(monkeypatch)
    assert await lease.acquire("job", 30)  # 释放后别人能拿


async def test_expired_lease_is_taken_over(client, monkeypatch):  # noqa: F811
    assert await lease.acquire("job", 30)
    async with SessionLocal() as s:  # 模拟持有者崩溃、租约过期
        await s.execute(update(Lease).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    as_other(monkeypatch)
    assert await lease.acquire("job", 30)
    async with SessionLocal() as s:
        row = await s.get(Lease, "job")
        assert row.holder == "other-host:1:abc"


async def test_hold_serializes_and_releases(client):  # noqa: F811
    order: list[str] = []

    async def worker(tag: str, holder: str):
        lease.HOLDER = holder  # 同一事件循环里模拟两个进程：只在拿锁前切换身份
        async with lease.hold("group:x", ttl=30, poll=0.05):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.15)
            order.append(f"{tag}-out")

    original = lease.HOLDER
    try:
        a = asyncio.create_task(worker("a", "proc-a"))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(worker("b", "proc-b"))
        await asyncio.gather(a, b)
    finally:
        lease.HOLDER = original
    assert order == ["a-in", "a-out", "b-in", "b-out"]
    async with SessionLocal() as s:
        assert (await s.scalars(select(Lease))).all() == []  # 用完即删，不留垃圾


async def test_recover_stuck_only_touches_old_claims(client, fake):  # noqa: F811
    cid, _ = await setup_groups(client)
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        fresh = Outbound(group_id=cid, purpose="manual", text="正在发", score=100,
                         status=OutboundStatus.sending, claimed_at=now)
        old = Outbound(group_id=cid, purpose="manual", text="卡住了", score=100,
                       status=OutboundStatus.sending, claimed_at=now - timedelta(minutes=10))
        s.add_all([fresh, old])
        await s.commit()
        assert await presend.recover_stuck(s) == 1
        await s.refresh(fresh)
        await s.refresh(old)
        assert fresh.status == OutboundStatus.sending  # 别的进程正在发，不能误伤
        assert old.status == OutboundStatus.failed


async def test_scheduler_runs_only_on_leader(client, monkeypatch):  # noqa: F811
    from app import main

    calls = []

    async def spy(_s):
        calls.append(lease.HOLDER)
        return 0

    for name in ("recover_stuck", "remind_suppliers", "flush_due", "close_idle", "auto_push",
                 "check_sla"):
        monkeypatch.setattr(main, name, spy)
    monkeypatch.setattr(main.settings, "scheduler_interval_seconds", 0.05)

    as_other(monkeypatch)
    assert await lease.acquire("scheduler", 60)  # 另一个副本已是主
    monkeypatch.setattr(lease, "HOLDER", "me:2:def")
    task = asyncio.create_task(main.scheduler())
    await asyncio.sleep(0.2)
    assert calls == []  # 待命，不重复执行
    async with SessionLocal() as s:  # 主副本挂了
        await s.execute(update(Lease).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    await asyncio.sleep(0.2)
    task.cancel()
    assert calls and set(calls) == {"me:2:def"}  # 自动接管

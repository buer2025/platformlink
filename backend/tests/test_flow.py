from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import Base, engine
from app.im.registry import set_adapter
from app.main import app
from app.models import Outbound
from app.services import presend


class FakeAdapter:
    platform = "telegram"

    def __init__(self):
        self.sent = []

    def parse_webhook(self, payload):
        return None

    async def send(self, chat_id, text, reply_to="", mentions=None):
        self.sent.append((chat_id, text, mentions))
        return str(len(self.sent))


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_customer_429_routes_to_supplier(client):
    fake = FakeAdapter()
    set_adapter("telegram", fake)

    # 第一条消息让群自动出现，默认只观察
    await client.post("/api/simulate", json={"chat_id": "c1", "chat_name": "客户A", "text": "hi"})
    await client.post("/api/simulate", json={"chat_id": "s1", "chat_name": "供应商Y", "text": "hi"})
    groups = {g["external_id"]: g for g in (await client.get("/api/groups")).json()}
    await client.patch(f"/api/groups/{groups['c1']['id']}", json={"automation": "auto"})
    await client.patch(f"/api/groups/{groups['s1']['id']}", json={
        "kind": "supplier", "models": ["seedance"], "contacts": ["yy_tech"]})

    r = await client.post("/api/simulate", json={
        "chat_id": "c1", "text": "seedance 一直 429，限流太严重了"})
    inc = r.json()
    assert inc["category"] == "rate_limit" and inc["responsible"] == "supplier"

    out = (await client.get("/api/outbound")).json()
    purposes = {o["purpose"]: o for o in out}
    assert purposes["ack"]["status"] == "pending"
    # 规则兜底置信度 55 < 70，必须人工确认
    assert purposes["customer_reply"]["status"] == "needs_approval"
    assert purposes["supplier_ticket"]["mentions"] == ["yy_tech"]

    # ack 倒计时为 0，调度器立即发送
    from app.db import SessionLocal
    async with SessionLocal() as s:
        assert await presend.flush_due(s) == 1
    assert fake.sent[0][0] == "c1"

    # 人工确认供应商工单
    r = await client.post(f"/api/outbound/{purposes['supplier_ticket']['id']}/send")
    assert r.json()["status"] == "sent"
    assert fake.sent[-1] == ("s1", purposes["supplier_ticket"]["text"], ["yy_tech"])

    # 已发送的不能再取消
    r = await client.post(f"/api/outbound/{purposes['supplier_ticket']['id']}/cancel")
    assert r.status_code == 409


async def test_countdown_auto_send(client):
    fake = FakeAdapter()
    set_adapter("telegram", fake)
    await client.post("/api/simulate", json={"chat_id": "c1", "text": "hi"})
    from app.db import SessionLocal
    from app.models import Group
    async with SessionLocal() as s:
        g = await s.get(Group, 1)
        g.automation = "auto"
        item = await presend.enqueue(s, g, "已恢复", 95, "customer_reply")
        assert item.status == "pending"
        await s.commit()
        assert await presend.flush_due(s) == 0  # 未到时间
        item.send_at = presend.utcnow() - timedelta(seconds=1)
        await s.commit()
        assert await presend.flush_due(s) == 1
        assert (await s.get(Outbound, item.id)).status == "sent"


def test_delay_tiers():
    from app.models import Group
    g = Group(automation="auto", extra_delay_seconds=0)
    assert presend.delay_for(95, g, "customer_reply") == 120
    assert presend.delay_for(75, g, "customer_reply") == 600
    assert presend.delay_for(50, g, "customer_reply") is None
    g.automation = "observe"
    assert presend.delay_for(100, g, "ack") is None

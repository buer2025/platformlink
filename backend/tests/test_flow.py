from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.im.registry import set_adapter
from app.main import app
from app.models import Group, Incident, Outbound
from app.services import presend

H = {"Authorization": "Bearer test-admin"}
TG = {"X-Telegram-Bot-Api-Secret-Token": "tg-secret"}


class FakeAdapter:
    platform = "telegram"

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def parse_webhook(self, payload):
        from app.im.telegram import TelegramAdapter
        return TelegramAdapter("x").parse_webhook(payload)

    async def send(self, chat_id, text, reply_to="", mentions=None):
        if self.fail:
            raise RuntimeError("network down")
        self.sent.append((chat_id, text, mentions))
        return f"out{len(self.sent)}"


@pytest.fixture
async def fake():
    f = FakeAdapter()
    set_adapter("telegram", f)
    return f


@pytest.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=H) as c:
        yield c


def tg(chat, mid, text, user="alice", reply_to=None, title="群"):
    msg = {"message_id": mid, "chat": {"id": chat, "title": title},
           "from": {"username": user}, "text": text}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"message": msg}


async def setup_groups(client):
    await client.post("/api/simulate", json={"chat_id": "100", "chat_name": "客户A", "text": "hi"})
    await client.post("/api/simulate", json={"chat_id": "200", "chat_name": "供应商Y", "text": "hi"})
    g = {x["external_id"]: x for x in (await client.get("/api/groups")).json()}
    await client.patch(f"/api/groups/{g['100']['id']}", json={"automation": "auto"})
    await client.patch(f"/api/groups/{g['200']['id']}", json={
        "kind": "supplier", "automation": "auto", "models": ["seedance"], "contacts": ["yy"]})
    return g["100"]["id"], g["200"]["id"]


async def outbound(client):
    return (await client.get("/api/outbound")).json()


# ---------- 鉴权 ----------

async def test_admin_requires_token(client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        assert (await anon.get("/api/groups")).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert (await anon.get("/api/groups", headers=bad)).status_code == 401
    assert (await client.get("/api/me")).status_code == 200


async def test_webhook_rejects_bad_secret(client, fake):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        r = await anon.post("/api/webhooks/telegram", json=tg(1, 1, "x"))
        assert r.status_code == 403
        r = await anon.post("/api/webhooks/telegram", json=tg(1, 1, "x"), headers=TG)
        assert r.status_code == 200
        r = await anon.post("/api/webhooks/feishu",
                            json={"type": "url_verification", "challenge": "c", "token": "fs-token"})
        assert r.json() == {"challenge": "c"}


# ---------- 主流程 ----------

async def test_customer_429_routes_to_supplier_and_tracks_reply(client, fake):
    cid, sid = await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 10, "seedance 一直 429，限流太严重了"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["category"] == "rate_limit" and inc["responsible"] == "supplier"
    assert inc["status"] == "waiting_supplier" and inc["supplier_group_id"] == sid

    out = {o["purpose"]: o for o in await outbound(client)}
    assert out["ack"]["status"] == "pending"
    assert out["customer_reply"]["status"] == "needs_approval"  # 兜底置信度 55 < 70
    assert out["supplier_ticket"]["mentions"] == ["yy"]

    async with SessionLocal() as s:
        assert await presend.flush_due(s) == 1  # ack 立即发出
    assert fake.sent[0][0] == "100"

    r = await client.post(f"/api/outbound/{out['supplier_ticket']['id']}/send")
    assert r.json()["status"] == "sent"
    ticket_ext = f"out{len(fake.sent)}"
    # 已发送的不能再取消
    r = await client.post(f"/api/outbound/{out['supplier_ticket']['id']}/cancel")
    assert r.status_code == 409

    # 供应商引用我们的转交消息回复“已扩容”
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(200, 20, "已扩好了，再试试", user="yy", reply_to=ticket_ext))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["supplier_replied_at"] and "已扩好" in inc["supplier_last_reply"]
    texts = [o for o in await outbound(client) if o["group_id"] == cid and o["status"] == "pending"
             and o["purpose"] == "customer_reply"]
    assert texts and texts[0]["score"] == 75  # 通知客户恢复，10 分钟倒计时


async def test_duplicate_webhook_and_merge(client, fake):
    await setup_groups(client)
    body = tg(100, 11, "seedance 报错 500 了")
    await client.post("/api/webhooks/telegram", headers=TG, json=body)
    await client.post("/api/webhooks/telegram", headers=TG, json=body)  # 平台重推
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 12, "还是 500 error"))
    incs = (await client.get("/api/incidents")).json()
    assert len(incs) == 1 and incs[0]["related_messages"] == 2
    assert len([o for o in await outbound(client) if o["purpose"] == "ack"]) == 1


async def test_staff_and_bot_messages_ignored(client, fake):
    await setup_groups(client)
    r = await client.post("/api/staff", json={"name": "小王", "platform": "telegram",
                                              "account": "wang"})
    assert r.status_code == 200
    assert (await client.post("/api/staff", json={"name": "x", "platform": "telegram",
                                                  "account": "wang"})).status_code == 409
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 13, "429 的问题我们在跟进", user="wang"))
    bot = tg(100, 14, "error 500")
    bot["message"]["from"]["is_bot"] = True
    await client.post("/api/webhooks/telegram", headers=TG, json=bot)
    assert (await client.get("/api/incidents")).json() == []


async def test_observe_group_never_replies(client, fake):
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(300, 1, "seedance 500 报错"))
    assert (await client.get("/api/incidents")).json() == []
    assert await outbound(client) == []


# ---------- 预发 ----------

async def test_countdown_auto_send(client, fake):
    cid, _ = await setup_groups(client)
    async with SessionLocal() as s:
        g = await s.get(Group, cid)
        item = await presend.enqueue(s, g, "已恢复", 95, "customer_reply")
        await s.commit()
        assert item.status == "pending"
        assert await presend.flush_due(s) == 0  # 未到时间
        item.send_at = presend.utcnow() - timedelta(seconds=1)
        await s.commit()
        assert await presend.flush_due(s) == 1
        assert (await s.get(Outbound, item.id)).status == "sent"


async def test_no_double_send(client, fake):
    cid, _ = await setup_groups(client)
    async with SessionLocal() as s:
        g = await s.get(Group, cid)
        item = await presend.enqueue(s, g, "hello", 95, "customer_reply")
        await s.commit()
        assert await presend.deliver(s, item) is True
        assert await presend.deliver(s, item) is False
    assert len(fake.sent) == 1
    assert (await client.post(f"/api/outbound/{item.id}/send")).status_code == 409


async def test_failed_send_can_retry(client):
    cid, _ = await setup_groups(client)
    set_adapter("telegram", FakeAdapter(fail=True))
    async with SessionLocal() as s:
        g = await s.get(Group, cid)
        item = await presend.enqueue(s, g, "hello", 95, "customer_reply")
        await s.commit()
    r = await client.post(f"/api/outbound/{item.id}/send")
    assert r.json()["status"] == "failed" and "network down" in r.json()["error"]
    set_adapter("telegram", FakeAdapter())
    r = await client.post(f"/api/outbound/{item.id}/send")
    assert r.json()["status"] == "sent"


async def test_edit_requires_confirmation(client, fake):
    cid, _ = await setup_groups(client)
    async with SessionLocal() as s:
        item = await presend.enqueue(s, await s.get(Group, cid), "a", 95, "customer_reply")
        await s.commit()
    r = await client.post(f"/api/outbound/{item.id}/edit", json={"text": "b"})
    assert r.json()["status"] == "needs_approval" and r.json()["send_at"] is None
    assert (await client.post(f"/api/outbound/{item.id}/edit", json={"text": " "})).status_code == 422


async def test_supplier_reminder(client, fake):
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 15, "seedance 429 太多"))
    ticket = next(o for o in await outbound(client) if o["purpose"] == "supplier_ticket")
    await client.post(f"/api/outbound/{ticket['id']}/send")
    async with SessionLocal() as s:
        assert await presend.remind_suppliers(s) == 0  # 未超时
        i = await s.scalar(select(Incident))
        i.ticket_sent_at = presend.utcnow() - timedelta(minutes=31)
        await s.commit()
        assert await presend.remind_suppliers(s) == 1
        assert await presend.remind_suppliers(s) == 0  # 下一次要再等 30 分钟
    reminder = next(o for o in await outbound(client) if o["purpose"] == "supplier_remind")
    assert reminder["mentions"] == ["yy"]


async def test_resolve_incident(client, fake):
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 16, "seedance 500 error"))
    inc = (await client.get("/api/incidents")).json()[0]
    r = await client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})
    assert r.json()["status"] == "resolved"
    msgs = (await client.get(f"/api/incidents/{inc['id']}/messages")).json()
    assert any("500" in m["text"] for m in msgs)


def test_delay_tiers():
    g = Group(automation="auto", extra_delay_seconds=0)
    assert presend.delay_for(95, g, "customer_reply") == 120
    assert presend.delay_for(75, g, "customer_reply") == 600
    assert presend.delay_for(50, g, "customer_reply") is None
    g.extra_delay_seconds = 60
    assert presend.delay_for(95, g, "customer_reply") == 180
    g.automation = "confirm"
    assert presend.delay_for(100, g, "customer_reply") is None
    g.automation = "observe"
    assert presend.delay_for(100, g, "ack") is None

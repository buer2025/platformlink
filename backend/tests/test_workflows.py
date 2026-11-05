"""链路 / 日志库（new-api 结构）/ 时间线 / 知识沉淀。"""

import os
import tempfile
import time

import pytest
from sqlalchemy import text

from app.agent import logs
from tests.test_flow import TG, client, fake, outbound, setup_groups, tg  # noqa: F401

NOW = int(time.time())


@pytest.fixture
async def logdb():
    path = os.path.join(tempfile.mkdtemp(), "newapi.db")
    db = logs.LogDB(f"sqlite+aiosqlite:///{path}")
    async with db.engine.begin() as c:
        # 与 new-api 一致的表结构（节选），注意 group 是保留字
        await c.execute(text(
            'CREATE TABLE logs (id INTEGER PRIMARY KEY, user_id INT, created_at BIGINT, type INT,'
            ' content TEXT, username TEXT, token_name TEXT, model_name TEXT, quota INT,'
            ' prompt_tokens INT, completion_tokens INT, use_time INT, is_stream BOOL,'
            ' channel_id INT, token_id INT, "group" TEXT, ip TEXT, other TEXT, request_id TEXT)'))
        await c.execute(text(
            "CREATE TABLE channels (id INTEGER PRIMARY KEY, type INT, name TEXT, status INT,"
            " response_time INT, models TEXT)"))
        await c.execute(text(
            "INSERT INTO channels VALUES (7, 1, 'seedance-上游B', 1, 820, 'seedance-1.0')"))
        rows = [
            (1, 42, NOW - 60, 5, "upstream error: status 503 service unavailable", "acme",
             "seedance-1.0-pro", 0, 0, 0, 7, '{"status_code":503}', "req-abc-123"),
            (2, 42, NOW - 120, 5, "upstream error: 503", "acme", "seedance-1.0-pro", 0, 0, 0, 7,
             "{}", "req-abc-124"),
            (3, 42, NOW - 300, 2, "", "acme", "seedance-1.0-pro", 500, 1000, 200, 7, "{}",
             "req-ok-1"),
        ]
        for r in rows:
            await c.execute(text(
                "INSERT INTO logs (id,user_id,created_at,type,content,username,model_name,quota,"
                "prompt_tokens,completion_tokens,channel_id,other,request_id)"
                " VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l,:m)"),
                dict(zip("abcdefghijklm", r, strict=True)))
    logs.set_logdb(db)
    yield db
    logs.set_logdb(None)
    await db.engine.dispose()


async def test_lookup_by_request_id_and_channel(client, logdb):  # noqa: F811
    r = await client.post("/api/lookup/test", json={
        "request_id": "req-abc-123", "api_users": ["acme"], "model": "seedance"})
    body = r.json()
    assert body["configured"] and body["found"]
    assert 7 in body["channel_ids"]
    assert "503" in body["text"] and "seedance-上游B" in body["text"]
    assert "错误 2 条" in body["text"]  # 近期错误
    assert "成功 1 次" in body["text"]  # 用量


async def test_lookup_without_logdb(client):  # noqa: F811
    body = (await client.post("/api/lookup/test", json={"request_id": "x"})).json()
    assert not body["configured"] and "未配置" in body["text"]


async def test_routes_supplier_by_channel_and_timeline(client, fake, logdb):  # noqa: F811
    cid, sid = await setup_groups(client)
    # 另建一个模型也匹配的供应商群，但渠道不对 —— 应该按渠道选中 sid
    await client.post("/api/simulate", json={"chat_id": "300", "chat_name": "供应商Z", "text": "hi"})
    z = next(g for g in (await client.get("/api/groups")).json() if g["external_id"] == "300")
    await client.patch(f"/api/groups/{z['id']}", json={"kind": "supplier", "models": ["seedance"]})
    await client.patch(f"/api/groups/{sid}", json={"channel_ids": [7], "models": []})
    await client.patch(f"/api/groups/{cid}", json={"api_users": ["acme"], "tags": ["VIP"]})

    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 11, "seedance 一直 503 报错 request_id: req-abc-123"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert "req-abc-123" in inc["lookup_result"] and "503" in inc["lookup_result"]
    # 规则兜底判断不出供应商（无 Key），手动验证路由函数
    from app.db import SessionLocal
    from app.services.pipeline import find_supplier_group
    async with SessionLocal() as s:
        g = await find_supplier_group(s, "seedance", [7])
        assert g.id == sid

    ev = (await client.get(f"/api/incidents/{inc['id']}/events")).json()
    kinds = [e["kind"] for e in ev]
    assert kinds[:3] == ["created", "classified", "queued"]
    assert "lookup" in kinds and "finding" in kinds

    r = await client.patch(f"/api/incidents/{inc['id']}",
                           json={"status": "resolved", "note": "上游已恢复", "actor": "小王"})
    assert r.json()["resolved_at"]
    ev = (await client.get(f"/api/incidents/{inc['id']}/events")).json()
    assert ev[-1]["kind"] == "note" and ev[-1]["actor"] == "小王"
    assert (await client.get("/api/incidents?status=active")).json() == []


async def test_workflow_match_and_confirm(client, fake):  # noqa: F811
    _cid, _ = await setup_groups(client)
    r = await client.post("/api/workflows", json={
        "name": "用量需求", "categories": ["usage_request"], "lookups": ["usage"],
        "automation": "confirm", "instructions": "先问目标量级"})
    wid = r.json()["id"]
    assert (await client.post("/api/workflows", json={"name": "用量需求"})).status_code == 409
    # 只对 VIP 群生效的链路，不应命中普通群
    await client.post("/api/workflows", json={
        "name": "VIP专线", "priority": 1, "categories": ["usage_request"], "group_tags": ["VIP"]})

    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 12, "seedance 下周想扩容到 500 RPM，能加量吗"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["category"] == "usage_request" and inc["workflow_id"] == wid
    out = {o["purpose"]: o for o in await outbound(client)}
    assert out["ack"]["status"] == "pending"
    assert out["customer_reply"]["status"] == "needs_approval"
    assert out["supplier_ticket"]["status"] == "needs_approval"  # 链路要求人工确认

    await client.delete(f"/api/workflows/{wid}")
    w = next(x for x in (await client.get("/api/workflows")).json() if x["id"] == wid)
    assert w["enabled"] is False


async def test_knowledge_is_used_and_drafted(client, fake):  # noqa: F811
    _cid, _ = await setup_groups(client)
    k = (await client.post("/api/knowledge", json={
        "title": "seedance 上游 429 需找供应商扩量", "symptoms": "429 too many requests",
        "keywords": ["429", "限流"], "models": ["seedance"], "responsible": "supplier",
        "solution": "转交供应商群要量"})).json()
    found = (await client.get("/api/knowledge", params={"q": "一直 429", "model": "seedance"})).json()
    assert found and found[0]["id"] == k["id"]

    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 13, "seedance 一直 429 限流"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["knowledge_ids"] == [k["id"]]
    assert (await client.get("/api/knowledge")).json()[0]["hits"] == 1

    d = (await client.post(f"/api/incidents/{inc['id']}/knowledge-draft")).json()
    assert d["source_incident_id"] == inc["id"] and d["models"] == ["seedance"]
    saved = (await client.post("/api/knowledge", json=d)).json()
    ev = (await client.get(f"/api/incidents/{inc['id']}/events")).json()
    assert any(e["kind"] == "knowledge_saved" and f"#{saved['id']}" in e["text"] for e in ev)


class ScriptedClaude:
    """按剧本返回 tool_use 的假客户端，用于验证工具循环。"""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    async def create(self, **kw):
        import anthropic.types as at
        self.calls.append(kw)
        name, args = self.script.pop(0)
        return at.Message.model_validate({
            "id": "m", "type": "message", "role": "assistant", "model": "x",
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "tool_use", "id": f"t{len(self.calls)}", "name": name,
                         "input": args}]})


async def test_agent_tool_loop(client, fake, logdb, monkeypatch):  # noqa: F811
    from app.agent import llm
    cid, sid = await setup_groups(client)
    await client.patch(f"/api/groups/{sid}", json={"channel_ids": [7], "models": []})
    await client.patch(f"/api/groups/{cid}", json={"api_users": ["acme"]})
    bot = ScriptedClaude([
        ("query_request_id", {"request_id": "req-abc-124"}),
        ("channel_status", {"channel_ids": [7]}),
        ("submit_finding", {
            "responsible": "supplier", "evidence": "渠道7 上游 503", "confidence": 92,
            "customer_reply": "上游有点波动，已经在催了", "supplier_note": "seedance 503 麻烦看下",
            "self_check": "也可能是我方路由，但只有渠道7报错"}),
    ])
    monkeypatch.setattr(llm, "_client", lambda: bot)
    monkeypatch.setattr(llm, "classify", lambda t: _cls())

    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 14, "seedance 报错 503"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["responsible"] == "supplier" and inc["confidence"] == 92
    assert inc["supplier_group_id"] == sid  # 由工具查到的渠道 7 路由
    ev = (await client.get(f"/api/incidents/{inc['id']}/events")).json()
    tools = [e["text"].split("(")[0] for e in ev if e["kind"] == "tool"]
    assert tools == ["query_request_id", "channel_status"]
    assert any("自检" in e["text"] for e in ev if e["kind"] == "finding")
    # 第二轮请求带回了第一轮的工具结果
    assert bot.calls[1]["messages"][2]["content"][0]["type"] == "tool_result"


async def test_agent_caps_unsupported_confidence(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    await setup_groups(client)
    bot = ScriptedClaude([("submit_finding", {
        "responsible": "customer", "evidence": "猜的", "confidence": 95,
        "customer_reply": "是你参数错了", "supplier_note": ""})])
    monkeypatch.setattr(llm, "_client", lambda: bot)
    monkeypatch.setattr(llm, "classify", lambda t: _cls())
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 15, "seedance 报错"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["confidence"] == 60  # 没有任何日志证据 → 封顶，不会被自动发出
    reply = next(o for o in await outbound(client) if o["purpose"] == "customer_reply")
    assert reply["status"] == "needs_approval"


async def _cls():
    from app.agent.llm import Classification
    return Classification(is_issue=True, category="error", model="seedance", request_id="",
                          summary="seedance 报错", confidence=90)


async def test_seed_defaults_are_complete(client):  # noqa: F811
    from app.db import SessionLocal
    from app.services.seed import seed_defaults
    async with SessionLocal() as s:
        await seed_defaults(s)
        await seed_defaults(s)  # 幂等
    rows = (await client.get("/api/workflows")).json()
    assert [w["name"] for w in rows] == ["模型调用问题", "模型用量需求"]
    assert rows[1]["group_tags"] == [] and rows[1]["automation"] == "confirm"


async def test_knowledge_substring_search(client):  # noqa: F811
    await client.post("/api/knowledge", json={"title": "客户反馈 seedance 一直 429", "symptoms": ""})
    assert len((await client.get("/api/knowledge", params={"q": "429"})).json()) == 1
    assert (await client.get("/api/knowledge", params={"q": "超时"})).json() == []


async def test_corrected_request_id_reinvestigates(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 21, "seedance 又 429 了 request_id: req-test-00"))
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 22, "seedance 又 429 了 request_id: req-test-003"))
    incs = (await client.get("/api/incidents")).json()
    assert len(incs) == 1 and incs[0]["request_id"] == "req-test-003"
    replies = [o for o in await outbound(client) if o["purpose"] == "customer_reply"]
    assert sorted(o["status"] for o in replies) == ["cancelled", "needs_approval"]
    acks = [o for o in await outbound(client) if o["purpose"] == "ack"]
    assert len(acks) == 1  # 不重复安抚
    ev = (await client.get(f"/api/incidents/{incs[0]['id']}/events")).json()
    assert any(e["kind"] == "reinvestigate" and "req-test-00 → req-test-003" in e["text"] for e in ev)
    # 同一个 request_id 再发一次：不重复排查，未发出的回复改写成整合版（始终只有一条待发）
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 23, "还是 429 request_id: req-test-003"))
    live = [o for o in await outbound(client) if o["purpose"] == "customer_reply" and o["status"] != "cancelled"]
    assert len(live) == 1 and live[0]["status"] == "needs_approval"



async def test_continuous_conversation(client, fake):  # noqa: F811
    """回复发出后：追问得到跟进回复（引用这条追问），致谢不回复，无关闲聊无事件时忽略。"""
    _cid, _ = await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 31, "seedance 报错了"))
    reply = next(o for o in await outbound(client) if o["purpose"] == "customer_reply")
    assert (await client.post(f"/api/outbound/{reply['id']}/send")).json()["status"] == "sent"

    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 32, "怎么样了？"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["related_messages"] == 2
    follow = [o for o in await outbound(client) if o["purpose"] == "customer_reply" and o["status"] != "sent"]
    assert len(follow) == 1 and "跟进" in follow[0]["text"]
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "followup" in ev

    await client.post(f"/api/outbound/{follow[0]['id']}/send")
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 33, "好的谢谢"))
    assert len([o for o in await outbound(client) if o["purpose"] == "customer_reply"]) == 2
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "no_reply" in ev

    # 事件解决后，闲聊不再触发任何东西
    await client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 34, "周末愉快"))
    assert len(await outbound(client)) == 3  # 安抚 + 回复 + 跟进；闲聊不新增


async def test_pending_reply_integrates_followups(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 41, "seedance 报错了"))
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 42, "急，在线等"))
    replies = [o for o in await outbound(client) if o["purpose"] == "customer_reply"]
    live = [o for o in replies if o["status"] != "cancelled"]
    assert len(live) == 1 and len(replies) == 2
    inc = (await client.get("/api/incidents")).json()[0]
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "integrated" in ev


async def test_style_learn_and_preview(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    cid, _ = await setup_groups(client)
    await client.post("/api/staff", json={"name": "小王", "platform": "telegram", "account": "wang"})
    r = await client.post(f"/api/groups/{cid}/style/learn")
    assert r.status_code == 422 and "至少需要 3 条" in r.json()["detail"]
    for i, t in enumerate(["王总好呀，这个我马上看哈~", "王总放心，已经在催上游啦", "好嘞王总，有消息第一时间同步您~"]):
        await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 60 + i, t, user="wang"))

    async def fake_learn(ours, theirs):
        assert len(ours) == 3
        return llm.StyleProfile(summary="熟络", address="王总", tone="熟络", particles=["哈", "~"],
                                emoji=[], phrases=["好嘞"], taboos=[], samples=ours[:2])
    monkeypatch.setattr(llm, "learn_style", fake_learn)
    body = (await client.post(f"/api/groups/{cid}/style/learn")).json()
    assert body["profile"]["address"] == "王总" and body["based_on"]["ours"] == 3

    # 无 Key 时预览至少会加上称呼
    r = await client.post(f"/api/groups/{cid}/style/preview",
                          json={"text": "已经在处理了", "style": {"address": "王总"}})
    assert r.json()["text"] == "王总，已经在处理了"
    assert "王总" in llm.describe_style(body["profile"])


async def test_quote_routes_to_that_incident(client, fake):  # noqa: F811
    """引用我方回复：追问 → 回复并引用；致谢 → 不回；引用已解决事件说又坏了 → 重新打开。"""
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 71, "seedance 报错了"))
    reply = next(o for o in await outbound(client) if o["purpose"] == "customer_reply")
    await client.post(f"/api/outbound/{reply['id']}/send")
    ours = f"out{len(fake.sent)}"  # 我方回复在平台上的消息 ID
    inc = (await client.get("/api/incidents")).json()[0]
    await client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})

    # 引用致谢：不回复，事件保持已解决
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 72, "👍", reply_to=ours))
    assert (await client.get(f"/api/incidents/{inc['id']}")).json()["status"] == "resolved"
    # 引用说还是不行：重新打开并跟进，回复引用这条追问
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 73, "还是不行啊", reply_to=ours))
    cur = (await client.get(f"/api/incidents/{inc['id']}")).json()
    assert cur["status"] == "investigating" and cur["related_messages"] == 3
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert ev.count("quoted") == 2 and "reopened" in ev and "no_reply" in ev and "followup" in ev
    assert len((await client.get("/api/incidents")).json()) == 1  # 没有开新事件


async def test_quote_unrelated_message_uses_context(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 81, "今天天气不错"))
    assert (await client.get("/api/incidents")).json() == []
    # 引用一条与事件无关的消息，但新消息本身是问题 → 正常分诊
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 82, "对了 seedance 报错了", reply_to=81))
    assert len((await client.get("/api/incidents")).json()) == 1


async def test_escalation_to_internal_group(client, fake):  # noqa: F811
    """Agent 说不清原因 → 在内部群 @ 负责人（用我方人员姓名换成平台账号），只升级一次。"""
    cid, _ = await setup_groups(client)
    await client.post("/api/staff", json={"name": "小王", "platform": "telegram", "account": "wang_tg"})
    await client.patch(f"/api/groups/{cid}", json={"owner": "小王", "backup_owner": "lee"})
    await client.post("/api/simulate", json={"chat_id": "900", "chat_name": "技术支持内部群", "text": "hi"})
    g = next(x for x in (await client.get("/api/groups")).json() if x["external_id"] == "900")
    await client.patch(f"/api/groups/{g['id']}", json={"kind": "internal"})  # 仍是只观察

    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 91, "seedance 报错了"))
    esc = [o for o in await outbound(client) if o["purpose"] == "escalation"]
    assert len(esc) == 1 and esc[0]["group_id"] == g["id"]
    assert esc[0]["mentions"] == ["wang_tg", "lee"] and esc[0]["status"] == "pending"
    assert "需要人工" in esc[0]["text"] and "准确率 40" in esc[0]["text"]
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["escalated_at"] and "准确率" in inc["escalation_reason"]
    # 再追问也不会重复升级
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 92, "怎么样了"))
    assert len([o for o in await outbound(client) if o["purpose"] == "escalation"]) == 1


async def test_escalation_in_customer_group_without_internal(client, fake):  # noqa: F811
    cid, _ = await setup_groups(client)
    await client.patch(f"/api/groups/{cid}", json={"owner": "zhang"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 95, "seedance 报错了"))
    esc = next(o for o in await outbound(client) if o["purpose"] == "escalation")
    assert esc["group_id"] == cid and esc["mentions"] == ["zhang"] and "负责的同事" in esc["text"]


async def test_acks_skip_ai(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    await setup_groups(client)

    async def boom(_):
        raise AssertionError("确认语不应调用分诊")
    monkeypatch.setattr(llm, "classify", boom)
    for i, t in enumerate(["好的", "👍", "收到~", "谢谢啦"]):
        await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 96 + i, t))


async def test_session_lifecycle_memory(client, fake, monkeypatch):  # noqa: F811
    """空闲自动关闭 → 沉淀记忆 → 新会话的 Agent 能看到记忆；手动解决也会沉淀。"""
    from datetime import timedelta

    from sqlalchemy import update

    from app.agent import harness
    from app.db import SessionLocal
    from app.models import Incident, IncidentEvent
    from app.services import memory, presend
    cid, _ = await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 111, "seedance 报错了"))
    inc = (await client.get("/api/incidents")).json()[0]

    async with SessionLocal() as s:  # 把时间拨回 2 天前
        old = presend.utcnow() - timedelta(days=2)
        await s.execute(update(IncidentEvent).values(created_at=old))
        await s.execute(update(Incident).values(created_at=old))
        await s.commit()
        assert await memory.close_idle(s) == 1
        assert await memory.close_idle(s) == 0
    cur = (await client.get(f"/api/incidents/{inc['id']}")).json()
    assert cur["status"] == "resolved"
    mems = (await client.get(f"/api/groups/{cid}/memories")).json()
    assert len(mems) == 1 and "seedance" in mems[0]["text"]
    kinds = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "auto_closed" in kinds and "memory_saved" in kinds

    # 新会话：开新事件，Agent 上下文带上记忆
    seen = {}

    async def spy(ctx, instructions="", preloaded=""):
        seen["memories"] = ctx.memories
        seen["prompt"] = harness._context(ctx, instructions, preloaded)
    monkeypatch.setattr(harness, "run", spy)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 112, "seedance 又报错了"))
    assert len((await client.get("/api/incidents")).json()) == 2
    assert seen["memories"] == [mems[0]["text"]] and "这个客户的过往" in seen["prompt"]

    # 手动解决也会沉淀；删除记忆
    new = (await client.get("/api/incidents")).json()[0]
    await client.patch(f"/api/incidents/{new['id']}", json={"status": "resolved"})
    mems = (await client.get(f"/api/groups/{cid}/memories")).json()
    assert len(mems) == 2
    await client.delete(f"/api/memories/{mems[0]['id']}")
    assert len((await client.get(f"/api/groups/{cid}/memories")).json()) == 1

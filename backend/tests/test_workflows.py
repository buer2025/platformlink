"""链路 / 日志库（new-api 结构）/ 时间线 / 知识沉淀。"""

import os
import tempfile
import time

import pytest
from sqlalchemy import text

from app.agent import logs
from tests.test_flow import TG, client, fake, outbound, setup_groups, tg  # noqa: F401

NOW = int(time.time())


async def login_as(client, name: str, role: str = "agent") -> dict:  # noqa: F811
    """建一个个人账号并登录，返回带 token 的请求头。"""
    username = f"u{abs(hash(name)) % 10**6}"
    await client.post("/api/users", json={"username": username, "display_name": name,
                                           "password": "password123", "role": role})
    r = await client.post("/api/auth/login", json={"username": username, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


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

    r = await client.patch(f"/api/incidents/{inc['id']}", headers=await login_as(client, "小王"),
                           json={"status": "resolved", "note": "上游已恢复"})
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

    async def parse(self, **kw):
        raise ValueError("剧本只覆盖工具循环")  # 其他 AI 调用走兜底

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
    # 只有一项直接证据（报障时段日志），横向对比缺失 → 准确率按证据封顶到 80
    assert inc["responsible"] == "supplier" and inc["confidence"] == 80
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
    return Classification(is_issue=True, category="error", model="seedance", request_id="", reply_kind="none",
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


async def test_acks_are_decided_by_ai(client, fake, monkeypatch):  # noqa: F811
    """「谢谢」「好的」交给 AI 判断：问题解决后的致谢可以自然地回一句，不走固定模板。"""
    from app.agent import llm
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 101, "seedance 报错了"))
    inc = (await client.get("/api/incidents")).json()[0]
    await client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})
    calls = []

    async def ai_followup(conv, new_text, context, quoted="", resolved=False):
        calls.append((new_text, resolved))
        return llm.FollowupDecision(should_reply=True, problem_persists=False,
                                    reason="客户致谢，礼貌回应", text="不客气～有事随时找我")
    monkeypatch.setattr(llm, "followup", ai_followup)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 102, "好了，谢谢！"))
    assert calls == [("好了，谢谢！", True)]
    last = (await outbound(client))[0]
    assert last["text"] == "不客气～有事随时找我" and last["purpose"] == "customer_reply"
    assert (await client.get(f"/api/incidents/{inc['id']}")).json()["status"] == "resolved"


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


async def test_manual_send_and_human_takeover(client, fake):  # noqa: F811
    """管理员后台发消息（可引用）→ 原样发送、记到事件；同事在群里亲自回复 → AI 让位。"""
    cid, _ = await setup_groups(client)
    await client.post("/api/staff", json={"name": "小王", "platform": "telegram", "account": "wang"})
    await client.patch(f"/api/groups/{cid}", json={"automation": "auto"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 121, "seedance 一直 429，限流太严重了"))
    inc = (await client.get("/api/incidents")).json()[0]
    customer_msg = next(m for m in (await client.get(f"/api/incidents/{inc['id']}/messages")).json()
                        if not m["from_us"])

    # 1) 后台人工发送，引用客户那条
    r = await client.post(f"/api/groups/{cid}/send", headers=await login_as(client, "小王"), json={
        "text": "我是小王，这个我来跟进", "incident_id": inc["id"],
        "reply_to_message_id": customer_msg["id"]})
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["purpose"] == "manual"
    assert fake.sent[-1][1] == "我是小王，这个我来跟进"
    cur = (await client.get(f"/api/incidents/{inc['id']}")).json()
    assert cur["human_by"] == "小王" and cur["human_until"]
    msgs = (await client.get(f"/api/incidents/{inc['id']}/messages")).json()
    assert any(m["sender"] == "后台·小王" for m in msgs)

    # 2) 人工接管期间，AI 新起草的回复不会自动发
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 122, "seedance 一直 429，request_id: req-x-1"))
    pend = [o for o in await outbound(client) if o["purpose"] == "customer_reply" and o["status"] != "cancelled"]
    assert pend and all(o["status"] == "needs_approval" for o in pend)


async def test_staff_reply_in_group_takes_over(client, fake):  # noqa: F811
    _cid, _ = await setup_groups(client)
    await client.post("/api/staff", json={"name": "小王", "platform": "telegram", "account": "wang"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 131, "seedance 报错了"))
    # 让一条 AI 回复处于倒计时
    reply = next(o for o in await outbound(client) if o["purpose"] == "ack")
    assert reply["status"] == "pending"
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 132, "我看下哈", user="wang"))
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["human_by"] == "小王"
    ack = next(o for o in await outbound(client) if o["id"] == reply["id"])
    assert ack["status"] == "needs_approval"
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "human_takeover" in ev


async def test_ai_draft(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    cid, _ = await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 141, "seedance 报错了"))
    inc = (await client.get("/api/incidents")).json()[0]
    seen = {}

    async def fake_compose(intent, context, style, fallback):
        seen.update(intent=intent, context=context)
        return "草稿"
    monkeypatch.setattr(llm, "compose", fake_compose)
    r = await client.post(f"/api/groups/{cid}/draft", json={"incident_id": inc["id"], "intent": "告诉他在扩容"})
    assert r.json()["text"] == "草稿" and seen["intent"] == "告诉他在扩容"
    assert "seedance 报错了" in seen["context"] and "处理进展" in seen["context"]


async def test_same_group_messages_are_serialized(client, fake, monkeypatch):  # noqa: F811
    """同群并发进来的两条：第二条等第一条排查完再处理 → 只有一个事件、一条待发回复。"""
    import asyncio

    from app.agent import llm
    from app.db import SessionLocal
    from app.im.telegram import TelegramAdapter
    from app.services.pipeline import handle_inbound
    await setup_groups(client)
    real = llm.investigate

    async def slow(*a, **kw):
        await asyncio.sleep(0.3)
        return await real(*a, **kw)
    monkeypatch.setattr(llm, "investigate", slow)
    ad = TelegramAdapter("x")

    async def go(mid, text):
        async with SessionLocal() as s:
            await handle_inbound(s, ad.parse_webhook(tg(100, mid, text)))
    await asyncio.gather(go(151, "seedance 报错了"), go(152, "seedance 还是报错，急"))
    assert len((await client.get("/api/incidents")).json()) == 1
    live = [o for o in await outbound(client) if o["purpose"] == "customer_reply" and o["status"] != "cancelled"]
    assert len(live) == 1


async def test_failure_escalates(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    cid, _ = await setup_groups(client)
    await client.patch(f"/api/groups/{cid}", json={"owner": "zhang"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 161, "seedance 报错了"))

    async def boom(*a, **kw):
        raise RuntimeError("AI 挂了")
    monkeypatch.setattr(llm, "classify", boom)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 162, "怎么样了"))
    inc = (await client.get("/api/incidents")).json()[0]
    ev = [e["kind"] for e in (await client.get(f"/api/incidents/{inc['id']}/events")).json()]
    assert "error" in ev
    assert any(o["purpose"] == "escalation" for o in await outbound(client))


async def test_learns_from_human_edits(client, fake):  # noqa: F811
    """同事改过 AI 草稿再发出 → 记录改法，后续改写时作为参考；发送方式可统计。"""
    from app.agent import llm
    from app.db import SessionLocal
    from app.models import Group
    from app.services.style import style_of
    cid, _ = await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 171, "seedance 报错了"))
    reply = next(o for o in await outbound(client) if o["purpose"] == "customer_reply")
    await client.post(f"/api/outbound/{reply['id']}/edit", json={"text": "王总，这个我来看，稍等哈", "editor": "小王"})
    sent = (await client.post(f"/api/outbound/{reply['id']}/send")).json()
    assert sent["status"] == "sent"
    async with SessionLocal() as s:
        st = await style_of(s, await s.get(Group, cid))
    assert st["corrections"][0]["human"] == "王总，这个我来看，稍等哈"
    assert st["corrections"][0]["ai"] == reply["text"]
    assert "同事改成：王总，这个我来看" in llm.describe_style(st)


async def test_stats(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 181, "seedance 报错了"))
    st = (await client.get("/api/stats")).json()
    assert st["incidents"] == 1 and st["waiting"][0]["last_message"] == "seedance 报错了"
    reply = next(o for o in await outbound(client) if o["purpose"] == "customer_reply")
    await client.post(f"/api/outbound/{reply['id']}/send")
    st = (await client.get("/api/stats")).json()
    assert st["waiting"] == [] and st["replies_sent"] == 1 and st["ai_adopted"] == 1
    assert st["first_response_median_min"] is not None and len(st["series"]) == 7


async def test_screenshot_is_read_into_message(client, fake, monkeypatch):  # noqa: F811
    """客户只发了一张报错截图：识别出的报错和 request_id 进入分诊与排查。"""
    from app.agent import llm
    from app.im.telegram import TelegramAdapter
    await setup_groups(client)
    p = TelegramAdapter("x").parse_webhook({"message": {
        "message_id": 191, "chat": {"id": 100, "title": "群"}, "from": {"username": "alice"},
        "photo": [{"file_id": "small"}, {"file_id": "big"}]}})
    assert p.image_ids == ["big"] and p.text == "[图片]"

    async def fetch(image_id):
        assert image_id == "big"
        return b"png", "image/png"
    fake.fetch_image = fetch

    async def read(data, mime):
        return "Error 429: seedance rate limit exceeded, request_id: req-shot-1"
    monkeypatch.setattr(llm, "read_image", read)
    await client.post("/api/webhooks/telegram", headers=TG, json={"message": {
        "message_id": 191, "chat": {"id": 100, "title": "群"}, "from": {"username": "alice"},
        "photo": [{"file_id": "small"}, {"file_id": "big"}], "caption": "又这样了"}})
    inc = (await client.get("/api/incidents")).json()[0]
    assert inc["request_id"] == "req-shot-1" and inc["category"] == "rate_limit"
    msgs = (await client.get(f"/api/incidents/{inc['id']}/messages")).json()
    assert "又这样了" in msgs[0]["text"] and "[截图内容]" in msgs[0]["text"]


async def test_accounts_roles_and_assignment(client, fake):  # noqa: F811
    """个人账号登录、权限、值班自动分配（负载最少）、指派、我的事件、升级 @ 被指派人。"""
    cid, _ = await setup_groups(client)
    await client.patch(f"/api/groups/{cid}", json={"backup_owner": "lee"})
    a = await login_as(client, "小王")
    b = await login_as(client, "小李")
    # 普通支持不能建账号 / 改链路
    assert (await client.post("/api/users", headers=a, json={
        "username": "x1", "display_name": "x", "password": "password123"})).status_code == 403
    assert (await client.post("/api/workflows", headers=a, json={"name": "x"})).status_code == 403
    assert (await client.get("/api/me", headers=a)).json()["name"] == "小王"
    assert (await client.post("/api/auth/login", json={"username": "nope", "password": "x"})).status_code == 401
    # 两人都值班 → 轮流分配
    await client.patch("/api/me", headers=a, json={"on_duty": True})
    await client.patch("/api/me", headers=b, json={"on_duty": True})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 201, "seedance 报错了"))
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 202, "另外 seedance 一直 429"))
    incs = (await client.get("/api/incidents")).json()
    assert len(incs) == 2 and {i["assignee_id"] for i in incs} == {
        (await client.get("/api/me", headers=a)).json()["id"], (await client.get("/api/me", headers=b)).json()["id"]}
    mine = (await client.get("/api/incidents?mine=true", headers=a)).json()
    assert len(mine) == 1
    # 升级时 @ 被指派人（姓名 → 我方人员账号）
    await client.post("/api/staff", json={"name": "小王", "platform": "telegram", "account": "wang_tg"})
    me_id = (await client.get("/api/me", headers=a)).json()["id"]
    await client.patch(f"/api/incidents/{mine[0]['id']}", headers=b, json={"assignee_id": me_id})
    await client.post("/api/simulate", json={"chat_id": "900", "chat_name": "内部群", "text": "hi"})
    g = next(x for x in (await client.get("/api/groups")).json() if x["external_id"] == "900")
    await client.patch(f"/api/groups/{g['id']}", json={"kind": "internal"})
    from app.db import SessionLocal
    from app.models import Group, Incident
    from app.services.escalation import escalate
    async with SessionLocal() as s:
        inc = await s.get(Incident, mine[0]["id"])
        inc.escalated_at = None
        await escalate(s, await s.get(Group, cid), inc, "测试")
        await s.commit()
    esc = next(o for o in await outbound(client) if o["purpose"] == "escalation")
    assert esc["mentions"] == ["wang_tg", "lee"]
    # 退出后 token 失效
    await client.post("/api/auth/logout", headers=a)
    assert (await client.get("/api/me", headers=a)).status_code == 401


async def test_feishu_encrypted_webhook(client, monkeypatch):  # noqa: F811
    from app.config import settings
    from tests.test_adapters import _feishu_encrypt
    monkeypatch.setattr(settings, "feishu_encrypt_key", "k3y")
    enc = _feishu_encrypt({"type": "url_verification", "token": "fs-token", "challenge": "c1"}, "k3y")
    r = await client.post("/api/webhooks/feishu", json={"encrypt": enc})
    assert r.json() == {"challenge": "c1"}
    bad = _feishu_encrypt({"type": "url_verification", "token": "wrong", "challenge": "c1"}, "k3y")
    assert (await client.post("/api/webhooks/feishu", json={"encrypt": bad})).status_code == 403
    assert (await client.post("/api/webhooks/feishu", json={"encrypt": "garbage"})).status_code == 400


async def test_sla_alerts(client, fake, monkeypatch):  # noqa: F811
    """客户等待超过阈值 → 内部群提醒被指派人 + 负责人；同一条消息只提醒一次；工作时间判断。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.config import settings
    from app.db import SessionLocal
    from app.models import Message
    from app.services import presend, sla
    cid, _ = await setup_groups(client)
    await client.patch(f"/api/groups/{cid}", json={"owner": "zhang", "sla_minutes": 5})
    await client.post("/api/simulate", json={"chat_id": "900", "chat_name": "内部群", "text": "hi"})
    g = next(x for x in (await client.get("/api/groups")).json() if x["external_id"] == "900")
    await client.patch(f"/api/groups/{g['id']}", json={"kind": "internal"})
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 211, "seedance 报错了"))
    work_hours = sla.in_work_hours
    monkeypatch.setattr(sla, "in_work_hours", lambda now=None: True)
    async with SessionLocal() as s:
        assert await sla.check_sla(s) == 0  # 刚发，没超时
        await s.execute(update(Message).values(created_at=presend.utcnow() - timedelta(minutes=6)))
        await s.commit()
        assert await sla.check_sla(s) == 1
        assert await sla.check_sla(s) == 0  # 同一条不重复提醒
    alert = next(o for o in await outbound(client) if o["group_id"] == g["id"] and "等待" in o["text"])
    assert "zhang" in alert["mentions"] and alert["status"] == "pending"

    # 工作时间判断（北京时间）
    monkeypatch.setattr(settings, "work_hours", "09:00-21:00")
    monkeypatch.setattr(settings, "work_days", [1, 2, 3, 4, 5])
    assert work_hours(datetime(2026, 11, 5, 3, 0, tzinfo=UTC))  # 周四 11:00 北京
    assert not work_hours(datetime(2026, 11, 5, 14, 0, tzinfo=UTC))  # 周四 22:00
    assert not work_hours(datetime(2026, 11, 7, 3, 0, tzinfo=UTC))  # 周六


async def test_quota_center(client, fake, logdb):  # noqa: F811
    """催量中心：按供应商汇总（日志库 429 + 未解决事件）；一键起草需人工确认；堆积时自动起草且有冷却。"""
    from app.db import SessionLocal
    from app.services import quota
    cid, sid = await setup_groups(client)
    await client.patch(f"/api/groups/{sid}", json={"channel_ids": [7], "style": {"address": "张哥"}})
    for i, t in enumerate(["seedance 一直 429", "seedance 又 429 了，限流", "seedance 429 太多了"]):
        await client.post("/api/simulate", json={"chat_id": f"c{i}", "chat_name": f"客户{i}", "text": "hi"})
        g = next(x for x in (await client.get("/api/groups")).json() if x["external_id"] == f"c{i}")
        await client.patch(f"/api/groups/{g['id']}", json={"automation": "auto"})
        await client.post("/api/simulate", json={"chat_id": f"c{i}", "text": t})
    view = next(v for v in (await client.get("/api/quota")).json() if v["group_id"] == sid)
    assert view["open_incidents"] == 3 and len(view["customers"]) == 3
    assert view["usage_24h"]["errors"] == 2  # 模拟日志里渠道 7 的错误

    async with SessionLocal() as s:
        assert await quota.auto_push(s) == 1
        assert await quota.auto_push(s) == 0  # 冷却中
    push = next(o for o in await outbound(client) if o["purpose"] == "quota_push")
    assert push["status"] == "needs_approval" and push["group_id"] == sid
    assert "张哥" in push["text"]
    r = await client.post(f"/api/quota/{sid}/push", json={"note": "客户要 300 RPM"})
    assert r.json()["purpose"] == "quota_push"
    assert (await client.post(f"/api/quota/{cid}/push", json={})).status_code == 404

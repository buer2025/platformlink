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

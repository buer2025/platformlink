"""长对话压缩：较早消息并入摘要，最近原话保留，增量更新、不每条都调模型。"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Incident, IncidentStatus, Message
from app.services import pipeline
from tests.test_flow import client, fake, setup_groups  # noqa: F401


async def _incident_with(cid: int, n: int) -> int:
    async with SessionLocal() as s:
        first = Message(group_id=cid, external_id=f"c-open-{cid}-{n}", sender="客户",
                        text="开场", from_us=False)
        s.add(first)
        await s.flush()
        inc = Incident(group_id=cid, message_id=first.id, status=IncidentStatus.investigating,
                       category="call_error", summary="长对话")
        s.add(inc)
        await s.flush()
        for i in range(n):
            s.add(Message(group_id=cid, incident_id=inc.id, external_id=f"c{inc.id}-{i}",
                          sender="客户", text=f"第{i}条 request_id r{i}", from_us=False))
        await s.commit()
        return inc.id


async def _add(cid: int, inc_id: int, start: int, n: int) -> None:
    async with SessionLocal() as s:
        for i in range(start, start + n):
            s.add(Message(group_id=cid, incident_id=inc_id, external_id=f"c{inc_id}-{i}",
                          sender="客户", text=f"第{i}条 request_id r{i}", from_us=False))
        await s.commit()


async def test_short_conversation_is_verbatim(client, fake):  # noqa: F811
    cid, _ = await setup_groups(client)
    inc_id = await _incident_with(cid, 5)
    async with SessionLocal() as s:
        text, latest, _ = await pipeline._conversation(s, await s.get(Incident, inc_id))
    assert text.count("\n") == 4 and "第0条" in text and latest == f"c{inc_id}-4"


async def test_long_conversation_compresses_incrementally(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    calls = []

    async def summarize(prev, transcript):
        calls.append((prev, transcript))
        return f"摘要v{len(calls)}"

    monkeypatch.setattr(llm, "summarize_conversation", summarize)
    cid, _ = await setup_groups(client)
    inc_id = await _incident_with(cid, 30)
    async with SessionLocal() as s:
        inc = await s.get(Incident, inc_id)
        text, _, _ = await pipeline._conversation(s, inc)
        await s.commit()
    assert "摘要v1" in text and "第29条" in text and "第18条" in text  # 最近 12 条原话
    assert "第17条" not in text  # 已并入摘要
    assert "第0条" in calls[0][1] and "第17条" in calls[0][1]

    # 再来几条：摘要之后积压的旧消息不足 10 条，不重新调用模型，原样带上
    await _add(cid, inc_id, 30, 5)
    async with SessionLocal() as s:
        text, _, _ = await pipeline._conversation(s, await s.get(Incident, inc_id))
    assert len(calls) == 1 and "第18条" in text and "第34条" in text

    # 积压够 10 条：在旧摘要基础上增量合并
    await _add(cid, inc_id, 35, 6)
    async with SessionLocal() as s:
        inc = await s.get(Incident, inc_id)
        text, _, _ = await pipeline._conversation(s, inc)
        await s.commit()
    assert len(calls) == 2 and calls[1][0] == "摘要v1" and "第18条" in calls[1][1]
    assert "摘要v2" in text and "第18条" not in text
    async with SessionLocal() as s:
        kinds = (await s.scalars(select(Message.id))).all()
        assert kinds  # sanity
        inc = await s.get(Incident, inc_id)
        assert inc.conv_summary == "摘要v2"


async def test_compression_without_ai_keeps_opening_and_recent(client, fake):  # noqa: F811
    cid, _ = await setup_groups(client)
    inc_id = await _incident_with(cid, 40)
    async with SessionLocal() as s:
        text, _, _ = await pipeline._conversation(s, await s.get(Incident, inc_id))
    assert "第0条" in text and "第1条" in text  # 最初的问题描述
    assert "中间省略" in text and "第10条" not in text
    assert "第39条" in text and "第28条" in text


# ---------- 没有进行中的问题时，客户招呼 / 咨询也要接一句 ----------

async def test_greeting_gets_immediate_reply(client, fake):  # noqa: F811
    from tests.test_flow import TG, outbound, tg
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 801, "在吗？"))
    out = await outbound(client)
    assert [o["purpose"] for o in out] == ["greeting"]
    assert out[0]["status"] == "pending"  # 低风险，倒计时 0 秒，调度器下一拍就发
    from app.db import SessionLocal as SL
    from app.services import presend
    async with SL() as s:
        assert await presend.flush_due(s) == 1
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 802, "今天天气不错"))
    assert len(await outbound(client)) == 1  # 闲聊不插话


async def test_question_without_knowledge_needs_human(client, fake, monkeypatch):  # noqa: F811
    from app.agent import llm
    from tests.test_flow import TG, outbound, tg

    async def cls(_t):
        return llm.Classification(is_issue=False, category="other", model="", request_id="",
                                  summary="", confidence=80, reply_kind="question")

    monkeypatch.setattr(llm, "classify", cls)
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 803, "你们能开专票吗"))
    out = await outbound(client)
    assert out[0]["purpose"] == "chat" and out[0]["status"] == "needs_approval"

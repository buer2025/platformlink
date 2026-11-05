"""模型调用留存与处理进度。"""

from types import SimpleNamespace

from app.agent import llm
from app.services import trace
from tests.test_flow import TG, client, fake, setup_groups, tg  # noqa: F401


class FakeMessages:
    """按 output_format 返回固定结果的假 Claude。"""

    def __init__(self):
        self.n = 0

    async def parse(self, **kw):
        self.n += 1
        fmt = kw["output_format"]
        if fmt is llm.Classification:
            out = llm.Classification(is_issue=True, category="error", model="seedance",
                                     request_id="", summary="seedance 报错", confidence=90,
                                     reply_kind="none")
        elif fmt is llm.Finding:
            out = llm.Finding(responsible="unknown", evidence="无日志", confidence=40,
                              customer_reply="方便给下 request_id 吗", supplier_note="",
                              self_check="没有日志")
        else:
            out = fmt.model_validate({k: "好的" if f.annotation is str else 0
                                      for k, f in fmt.model_fields.items()})
        return SimpleNamespace(parsed_output=out, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                     cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0),
                               content=[])

    async def create(self, **kw):
        raise RuntimeError("relay down")  # 排查 Agent 失败 → 记录为 error 并回退


def _fake_claude(monkeypatch):
    inner = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(llm, "_clients", {"k": trace.TracedClient(inner)})
    return inner


async def test_every_model_call_is_logged_and_linked(client, fake, monkeypatch):  # noqa: F811
    _fake_claude(monkeypatch)
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 901, "seedance 报错了"))
    await trace.flush()
    inc = (await client.get("/api/incidents")).json()[0]
    calls = (await client.get(f"/api/incidents/{inc['id']}/model-calls")).json()
    kinds = [c["kind"] for c in calls]
    assert kinds[0] == "classify"  # 建事件之前的分诊也归到这个事件（通过触发消息关联）
    assert "agent" in kinds and "compose" in kinds
    agent = next(c for c in calls if c["kind"] == "agent")
    assert agent["status"] == "error" and "relay down" in agent["error"] and agent["step"] == 1
    ok = next(c for c in calls if c["kind"] == "classify")
    assert ok["status"] == "ok" and ok["input_tokens"] == 100 and ok["provider"] == "claude"

    detail = (await client.get(f"/api/model-calls/{ok['id']}")).json()
    assert "seedance 报错了" in detail["request"] and "is_issue" in detail["response"]

    stats = (await client.get("/api/model-calls/stats")).json()
    claude = next(x for x in stats if x["provider"] == "claude")
    assert claude["calls"] == len(calls) and claude["errors"] >= 1

    assert (await client.get("/api/model-calls?status=error")).json()[0]["kind"] == "agent"


async def test_jev_calls_are_logged(client, monkeypatch):  # noqa: F811
    import httpx

    from app.agent import jev

    async def handler(request):
        return httpx.Response(200, json={"model": "jev-1.14.0", "usage": {"input_tokens": 300},
                                         "answers": {"is_issue": {"type": "noul", "noul": 0.03},
                                                     "category": {"choice": "other"},
                                                     "reply_kind": {"choice": "none"}}})

    monkeypatch.setattr(jev, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm.settings, "jev_model", "jev-1.14.0")
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "k")
    with trace.scope(group_id=42):
        c = await llm.classify("今天天气不错")
    assert not c.is_issue
    await trace.flush()
    calls = (await client.get("/api/model-calls?provider=jev")).json()
    assert calls[0]["kind"] == "classify" and calls[0]["group_id"] == 42
    assert calls[0]["input_tokens"] == 300 and calls[0]["status"] == "ok"


async def test_live_progress(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG,
                      json=tg(100, 902, "seedance 一直报 429 request_id: req-1234567"))
    inc = (await client.get("/api/incidents")).json()[0]
    live = (await client.get(f"/api/incidents/{inc['id']}/live")).json()
    st = {x["key"]: x["state"] for x in live["stages"]}
    assert st["received"] == "done" and st["triage"] == "done" and st["investigate"] == "done"
    assert list(st.values()).count("active") == 1
    assert live["latest"] and live["event_count"] > 3

    await client.patch(f"/api/incidents/{inc['id']}", json={"status": "resolved"})
    live = (await client.get(f"/api/incidents/{inc['id']}/live")).json()
    assert live["stages"][-1]["state"] == "done"
    assert "active" not in [x["state"] for x in live["stages"]]

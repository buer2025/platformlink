"""操作审计与数据库查询耗时。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.agent import logs
from app.db import SessionLocal, engine
from app.main import _job, app
from app.models import AuditLog, DbQuery
from app.services import audit, retention, trace
from tests.test_flow import TG, client, fake, setup_groups, tg  # noqa: F401
from tests.test_workflows import logdb  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_queue():
    audit._queue.clear()
    audit.instrument(engine, "app")  # 测试不跑 lifespan，这里手动挂上计时
    yield
    audit._queue.clear()


async def _rows(**where) -> list[AuditLog]:
    await audit.flush()
    async with SessionLocal() as s:
        q = select(AuditLog).order_by(AuditLog.id)
        for k, v in where.items():
            q = q.where(getattr(AuditLog, k) == v)
        return list((await s.scalars(q)).all())


async def test_admin_write_is_audited_with_secrets_masked(client):  # noqa: F811
    r = await client.post("/api/users", json={"username": "bob", "display_name": "Bob",
                                              "password": "hunter2-secret", "role": "agent"})
    assert r.status_code == 200
    [row] = await _rows(action="新建账号")
    assert row.actor == "管理员（ADMIN_TOKEN）" and row.role == "admin" and row.source == "http"
    assert row.method == "POST" and row.status == 200 and row.ok
    assert "hunter2" not in row.request and '"password": "***"' in row.request and "bob" in row.request
    assert row.db_count > 0 and row.latency_ms >= 0


async def test_login_attempts_and_reads(client):  # noqa: F811
    await client.post("/api/users", json={"username": "bob", "display_name": "Bob",
                                          "password": "pw-123456", "role": "agent"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        assert (await anon.post("/api/auth/login", json={"username": "bob", "password": "wrong"})).status_code == 401
        ok = await anon.post("/api/auth/login", json={"username": "bob", "password": "pw-123456"})
        assert (await anon.get("/api/groups")).status_code == 401  # 未登录访问也要留痕
        await anon.get("/api/groups", headers={"Authorization": f"Bearer {ok.json()['token']}"})
    bad, good = await _rows(action="登录")
    assert not bad.ok and bad.status == 401 and bad.actor == "bob（尝试）" and "wrong" not in bad.request
    assert good.ok and good.actor == "Bob" and good.role == "agent"
    denied = [r for r in await _rows(method="GET")]
    assert len(denied) == 1 and denied[0].actor == "未登录" and denied[0].status == 401  # 成功的只读请求默认不记


async def test_support_cannot_read_audit(client):  # noqa: F811
    await client.post("/api/users", json={"username": "bob", "display_name": "Bob",
                                          "password": "pw-123456", "role": "agent"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        tok = (await anon.post("/api/auth/login", json={"username": "bob", "password": "pw-123456"})).json()["token"]
        r = await anon.get("/api/audit", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert (await client.get("/api/audit")).status_code == 200


async def test_webhook_and_agent_processing_are_audited(client, fake):  # noqa: F811
    await setup_groups(client)
    await client.post("/api/webhooks/telegram", headers=TG, json=tg(100, 901, "seedance 500 报错了"))
    hook = (await _rows(source="webhook"))[-1]
    assert hook.actor == "telegram" and hook.ok and "原文见群消息记录" in hook.request
    agent = [r for r in await _rows(source="agent") if "seedance 500" in r.detail][-1]
    assert agent.action == "处理群消息" and agent.actor == "Agent" and agent.ok
    assert agent.incident_id and f"事件 #{agent.incident_id}" in agent.detail and agent.db_count > 0
    detail = (await client.get(f"/api/audit/{agent.id}")).json()
    assert detail["detail"] == agent.detail


async def test_newapi_queries_are_timed_and_attributed(client, logdb):  # noqa: F811
    audit._queue.clear()  # 去掉建测试数据时的插入
    async with trace.scope(incident_id=42), audit.op("agent", "排查") as o:
        ev = logs.Evidence()
        await logs.by_request_id(logdb, "req-abc-123", ev)
    assert ev.found
    await audit.flush()
    qs = (await client.get(f"/api/db-queries?db=newapi&trace_id={o.trace_id}")).json()
    assert qs and all(q["incident_id"] == 42 and q["duration_ms"] >= 0 for q in qs)
    assert any(q["caller"] == "logs.by_request_id" and q["op"] == "select" for q in qs)
    assert any("req-abc-123" in q["params"] for q in qs)
    assert (await client.get("/api/incidents/42/db-queries")).json()
    [row] = await _rows(action="排查")
    assert row.db_count >= len(qs) and row.db_ms >= 0
    stats = (await client.get("/api/db-queries/stats")).json()
    assert any(d["db"] == "newapi" and d["count"] >= len(qs) for d in stats["dbs"])


async def test_slow_app_queries_logged_but_not_audit_writes(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(audit.settings, "slow_query_ms", 0)
    await client.patch("/api/me", json={"on_duty": True})  # 内置管理员不能改 → 400，也要记
    await client.post("/api/staff", json={"name": "张三", "platform": "telegram", "account": "zs"})
    await audit.flush()
    async with SessionLocal() as s:
        stmts = list((await s.scalars(select(DbQuery.statement).where(DbQuery.db == "app"))).all())
    assert any("staff" in x for x in stmts)
    assert not any("audit_logs" in x or "db_queries" in x for x in stmts)  # 不记录审计自己的写入
    [row] = await _rows(action="修改个人设置")
    assert row.status == 400 and not row.ok


async def test_scheduler_jobs_audited_only_when_they_act(client):  # noqa: F811
    async def nothing(_s):
        return 0

    async def two(_s):
        audit.note("预发消息 #1 已发送")
        return 2

    async def boom(_s):
        raise RuntimeError("db down")

    async with SessionLocal() as s:
        await _job(s, "空转", nothing, "做了 {} 件")
        await _job(s, "发送", two, "发送 {} 条")
        with pytest.raises(RuntimeError):
            await _job(s, "出错", boom, "{}")
    rows = await _rows(source="scheduler")
    assert [r.action for r in rows] == ["发送", "出错"]
    assert "预发消息 #1" in rows[0].detail and "发送 2 条" in rows[0].detail
    assert not rows[1].ok and "db down" in rows[1].detail


async def test_retention_purges_only_when_configured(client, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from app.services.presend import utcnow

    async with SessionLocal() as s:
        s.add(AuditLog(trace_id="x", source="system", action="旧", created_at=utcnow() - timedelta(days=40)))
        s.add(AuditLog(trace_id="y", source="system", action="新"))
        await s.commit()
        assert await retention.purge(s) == 0  # 默认永久保留
        monkeypatch.setattr(retention.settings, "record_retention_days", 30)
        assert await retention.purge(s) == 1
    assert [r.action for r in await _rows(source="system")] == ["新"]


def test_query_params_masking():
    assert audit._params("SELECT * FROM api_sessions WHERE token_hash = ?", ("abc",)) == "***"
    assert audit._params("UPDATE users SET password_hash=?", ("x",)) == "***"
    assert "req-1" in audit._params("SELECT prompt_tokens, token_name FROM logs WHERE request_id = ?", ("req-1",))

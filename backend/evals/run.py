"""跑评估：uv run python -m evals.run （需要配置 ANTHROPIC_API_KEY）

输出每条用例的结果和总分；低于阈值时退出码非 0，可接 CI / 发布前检查。
"""

import asyncio
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP}/eval.db"
os.environ["LOG_DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP}/newapi.db"

from sqlalchemy import text

from app.agent import harness, llm, logs
from app.db import Base, SessionLocal, engine
from app.models import Group, Incident, Message, Platform
from evals import cases

PASS_RATE = float(os.environ.get("EVAL_PASS_RATE", "0.85"))
results: list[tuple[str, str, bool, str]] = []


def check(kind: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((kind, name, ok, detail))
    print(f"{'✓' if ok else '✗'} [{kind}] {name}" + (f"  — {detail}" if detail and not ok else ""))


async def setup_logs() -> None:
    db = logs.get_logdb()
    async with db.engine.begin() as c:
        await c.execute(text(
            'CREATE TABLE logs (id INTEGER PRIMARY KEY, user_id INT, created_at BIGINT, type INT, content TEXT,'
            ' username TEXT, model_name TEXT, quota INT, prompt_tokens INT, completion_tokens INT,'
            ' channel_id INT, other TEXT, request_id TEXT, "group" TEXT)'))
        await c.execute(text("CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, status INT, response_time INT)"))
        await c.execute(text("INSERT INTO channels VALUES (7,'上游B',1,2300),(8,'上游C',1,640)"))
        now = int(time.time())
        for i, (rid, typ, content, ch) in enumerate(cases.LOGS):
            await c.execute(text(
                "INSERT INTO logs (user_id,created_at,type,content,username,model_name,quota,channel_id,other,request_id)"
                " VALUES (42,:t,:ty,:c,'acme','seedance-1.0-pro',0,:ch,'{}',:r)"),
                {"t": now - 60 * (i + 1), "ty": typ, "c": content, "ch": ch, "r": rid})


async def investigate(session, group, msg: str):
    c = await llm.classify(msg)
    m = Message(group_id=group.id, text=msg, sender="eval", external_id=f"eval-{id(msg)}-{time.time_ns()}")
    session.add(m)
    await session.flush()
    inc = Incident(group_id=group.id, message_id=m.id, category=c.category, model=c.model,
                   request_id=c.request_id, summary=c.summary)
    session.add(inc)
    await session.flush()
    ctx = harness.Ctx(session=session, group=group, incident=inc, c=c, text=msg)
    return c, await harness.run(ctx)


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY") and not llm.settings.anthropic_api_key:
        print("需要 ANTHROPIC_API_KEY")
        return 2
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await setup_logs()
    async with SessionLocal() as s:
        g = Group(platform=Platform.telegram, external_id="eval", name="评估群", models=["seedance"],
                  contacts=[], style={}, tags=[], api_users=["acme"], channel_ids=[])
        s.add(g)
        await s.flush()

        for msg, cat, resp, is_issue in cases.TRIAGE:
            c = await llm.classify(msg)
            check("分诊", msg, c.is_issue == is_issue and (cat is None or c.category == cat),
                  f"is_issue={c.is_issue} category={c.category}")
            if resp:
                _, f = await investigate(s, g, msg)
                ok = f is not None and f.responsible == resp
                check("责任方", msg, ok, f"得到 {f and f.responsible}（准确率 {f and f.confidence}）")
                if f and f.customer_reply:
                    leak = any(w in f.customer_reply for w in ("渠道", "channel", "上游B", "上游C"))
                    check("不泄露内部信息", msg, not leak, f.customer_reply[:80])

        for msg in cases.LOW_CONFIDENCE:
            _, f = await investigate(s, g, msg)
            check("证据不足封顶", msg, f is not None and f.confidence < 70, f"准确率 {f and f.confidence}")

        conv = "[14:02] 客户: seedance 一直报 429\n[14:03] 我方: 上游限流，已协调扩容，稍后同步"
        for msg, resolved, reply, persists in cases.FOLLOWUP:
            ctx = "状态: 已解决" if resolved else "状态: 已转交上游供应商，等待处理\n供应商回复: 暂未回复"
            d = await llm.followup(conv, msg, ctx, resolved=resolved)
            check("跟进判断", msg, d.should_reply == reply and d.problem_persists == persists,
                  f"回复={d.should_reply} 仍在={d.problem_persists} {d.reason}")

        for t in cases.STYLE_TEXTS:
            out = await llm.stylize(t, cases.STYLE, "customer_reply")
            bad = [w for w in cases.STYLE["taboos"] if w in out]
            n_particles = sum(out.count(p) for p in cases.STYLE["particles"])
            check("风格禁忌与克制", t, not bad and n_particles <= 2 and "李姐" in out, out)

    passed = sum(1 for r in results if r[2])
    rate = passed / len(results)
    print(f"\n通过 {passed}/{len(results)} = {rate:.0%}（阈值 {PASS_RATE:.0%}）")
    return 0 if rate >= PASS_RATE else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

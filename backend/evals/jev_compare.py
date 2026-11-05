"""Jev vs Claude：在同一批决策用例上对比准确率与耗时。

    uv run python -m evals.jev_compare
"""

import asyncio
import statistics
import sys
import time

from app.agent import jev, llm
from app.config import settings
from evals import cases

THRESHOLD = 0.5


async def timed(coro):
    t0 = time.monotonic()
    r = await coro
    return r, (time.monotonic() - t0) * 1000


async def main() -> int:
    if not settings.jev_model:
        settings.jev_model = "jev-1.14.0"
    model = sys.argv[1] if len(sys.argv) > 1 else settings.jev_model
    settings.jev_model = model
    rows = []  # (类别, 用例, claude_ok, jev_ok, claude_ms, jev_ms, jev_detail)

    triage = [(m, cat, issue, None) for m, cat, _, issue in cases.TRIAGE]
    triage += [(m, None, False, kind) for m, kind in cases.REPLY_KIND]
    for msg, cat, issue, kind in triage:
        (c, cms), (a, jms) = await asyncio.gather(
            timed(llm.classify(msg)), timed(jev.decide(jev.triage_state(msg), jev.TRIAGE)))
        c_ok = c.is_issue == issue and (cat is None or c.category == cat) and (
            kind is None or c.reply_kind == kind)
        if a is None:
            rows.append(("分诊", msg, c_ok, False, cms, jms, "调用失败"))
            continue
        j_issue = a["is_issue"]["noul"] >= THRESHOLD
        j_ok = j_issue == issue and (cat is None or a["category"]["choice"] == cat) and (
            kind is None or a["reply_kind"]["choice"] == kind)
        rows.append(("分诊", msg, c_ok, j_ok, cms, jms,
                     f"issue={a['is_issue']['noul']:.2f} cat={a['category']['choice']} "
                     f"kind={a['reply_kind']['choice']}"))

    conv = "[14:02] 客户: seedance 一直报 429\n[14:03] 我方: 上游限流，已协调扩容，稍后同步"
    fu = [(m, r, rep, per, "") for m, r, rep, per in cases.FOLLOWUP]
    fu += [(m, False, rep, per, q) for q, m, rep, per in cases.QUOTE]
    for msg, resolved, reply, persists, quoted in fu:
        ctx = "状态: 已解决" if resolved else "状态: 已转交上游供应商，等待处理\n供应商回复: 暂未回复"
        (d, cms), (a, jms) = await asyncio.gather(
            timed(llm.followup(conv, msg, ctx, quoted=quoted, resolved=resolved)),
            timed(jev.decide(jev.followup_state(conv, msg, ctx, quoted), jev.FOLLOWUP)))
        c_ok = d.should_reply == reply and (persists is None or d.problem_persists == persists)
        if a is None:
            rows.append(("跟进", msg, c_ok, False, cms, jms, "调用失败"))
            continue
        jr, jp = a["should_reply"]["noul"], a["problem_persists"]["noul"]
        j_ok = (jr >= THRESHOLD) == reply and (persists is None or (jp >= THRESHOLD) == persists)
        rows.append(("跟进", f"{msg}" + (f"（引用「{quoted}」）" if quoted else ""), c_ok, j_ok,
                     cms, jms, f"reply={jr:.2f} persists={jp:.2f}"))

    for kind, name, c_ok, j_ok, cms, jms, detail in rows:
        print(f"{'✓' if c_ok else '✗'}C {'✓' if j_ok else '✗'}J  {cms:5.0f}ms {jms:5.0f}ms  "
              f"[{kind}] {name}" + ("" if j_ok else f"   ← jev: {detail}"))
    n = len(rows)
    print(f"\n用例 {n} 条（{model}）")
    print(f"Claude ({settings.anthropic_model}): 准确 {sum(r[2] for r in rows)}/{n}，"
          f"耗时中位数 {statistics.median(r[4] for r in rows):.0f}ms")
    print(f"Jev: 准确 {sum(r[3] for r in rows)}/{n}，耗时中位数 {statistics.median(r[5] for r in rows):.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""催量中心：按供应商汇总 429 / 用量需求，个性化地找供应商要量。"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm, logs
from app.models import Group, GroupKind, Incident, IncidentStatus, Outbound
from app.services import presend, trace
from app.services.events import record
from app.services.style import style_of

log = logging.getLogger(__name__)
AUTO_THRESHOLD = 3  # 同一供应商未解决的 429 / 用量事件达到这么多，自动起草催量
AUTO_COOLDOWN = timedelta(hours=2)


async def _supplier_view(session: AsyncSession, g: Group) -> dict:
    incs = (await session.scalars(select(Incident).where(
        Incident.supplier_group_id == g.id, Incident.status != IncidentStatus.resolved,
        Incident.category.in_(["rate_limit", "usage_request"])).order_by(Incident.id.desc()))).all()
    customers = {}
    for i in incs:
        c = await session.get(Group, i.group_id)
        if c:
            customers.setdefault(c.name, []).append(i.summary)
    usage = {"rate_limited": 0, "errors": 0, "success": 0}
    if (db := logs.get_logdb()) and g.channel_ids:
        try:
            usage = await logs.channel_429(db, g.channel_ids)
        except Exception:
            log.warning("查询渠道 429 失败", exc_info=True)
    last = await session.scalar(select(Outbound).where(
        Outbound.group_id == g.id, Outbound.purpose == "quota_push").order_by(Outbound.id.desc()))
    return {
        "group_id": g.id, "name": g.name, "models": g.models, "channel_ids": g.channel_ids,
        "open_incidents": len(incs), "incident_ids": [i.id for i in incs],
        "customers": [{"name": k, "issues": v[:3]} for k, v in customers.items()],
        "usage_24h": usage,
        "last_push": {"at": presend.aware(last.created_at), "status": last.status, "text": last.text} if last else None,
    }


async def overview(session: AsyncSession) -> list[dict]:
    groups = (await session.scalars(select(Group).where(Group.kind == GroupKind.supplier)
                                    .order_by(Group.id))).all()
    rows = [await _supplier_view(session, g) for g in groups]
    return sorted(rows, key=lambda r: (-r["open_incidents"], -r["usage_24h"]["rate_limited"]))


async def draft_push(session: AsyncSession, supplier: Group, note: str = "") -> Outbound:
    """起草一条催量消息进入预发队列（必须人工确认）。"""
    trace.bind(group_id=supplier.id)
    v = await _supplier_view(session, supplier)
    u = v["usage_24h"]
    facts = [f"模型: {', '.join(supplier.models) or '未标记'}",
             f"最近 24 小时：429 限流 {u['rate_limited']} 次，错误 {u['errors']} 次，成功 {u['success']} 次",
             f"受影响客户 {len(v['customers'])} 家，未解决的问题 {v['open_incidents']} 个"]
    facts += [f"- {c['name']}：{'；'.join(c['issues'])}" for c in v["customers"][:5]]
    if note:
        facts.append(f"同事补充：{note}")
    text = await llm.compose(
        "找供应商要量：说明我们这边被限流的情况和对客户的影响，请对方帮忙扩并发 / 提额度，"
        "问一下大概多久能扩好。语气像合作很久的老朋友，客气但直接，不要像系统通知；"
        "不要写客户公司名，用「几家客户」概括；数字照实写",
        "\n".join(facts), await style_of(session, supplier),
        f"{supplier.style.get('address', '') if supplier.style else ''}你好，我们这边 {', '.join(supplier.models)} "
        f"最近限流比较多（24 小时 429 约 {u['rate_limited']} 次），影响到几家客户，麻烦帮忙看下能不能扩一下量，大概多久能好？")
    item = await presend.enqueue(session, supplier, text, 80, "quota_push", None,
                                 mentions=supplier.contacts, force_confirm=True)
    for iid in v["incident_ids"]:
        record(session, iid, "quota_push", f"已起草催量消息给 {supplier.name}，等待确认发送")
    return item


async def auto_push(session: AsyncSession) -> int:
    """429 堆积时自动起草催量（仍需人工确认），同一供应商 2 小时内最多一次。"""
    n = 0
    for v in await overview(session):
        if v["open_incidents"] < AUTO_THRESHOLD:
            continue
        if v["last_push"] and v["last_push"]["at"] > presend.utcnow() - AUTO_COOLDOWN:
            continue
        await draft_push(session, await session.get(Group, v["group_id"]))
        n += 1
    await session.commit()
    return n

"""数据看板：响应速度、解决速度、AI 采用率、人工修改率、升级率，以及正在等回复的客户。"""

from collections import Counter
from datetime import timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Incident, IncidentStatus, Message, Outbound, OutboundStatus
from app.services.presend import aware, utcnow

TO_CUSTOMER = ("ack", "customer_reply", "manual", "escalation", "greeting", "chat")


def _mins(a, b) -> float:
    return round((aware(b) - aware(a)).total_seconds() / 60, 1)


async def overview(session: AsyncSession, days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    incidents = [i for i in (await session.scalars(select(Incident))).all() if aware(i.created_at) >= since]
    ids = {i.id for i in incidents}
    outs = (await session.scalars(select(Outbound).where(Outbound.status == OutboundStatus.sent))).all()
    outs = [o for o in outs if o.incident_id in ids]

    first: dict[int, object] = {}
    for o in sorted(outs, key=lambda x: x.sent_at):
        inc = next(i for i in incidents if i.id == o.incident_id)
        if o.group_id == inc.group_id and o.purpose in TO_CUSTOMER and o.incident_id not in first:
            first[o.incident_id] = o.sent_at
    frt = [_mins(i.created_at, first[i.id]) for i in incidents if i.id in first]
    res = [_mins(i.created_at, i.resolved_at) for i in incidents if i.resolved_at]

    replies = [o for o in outs if o.purpose == "customer_reply"]
    edited = [o for o in replies if o.original_text and o.original_text.strip() != o.text.strip()]
    adopted = [o for o in replies if o not in edited]
    manual = [o for o in outs if o.purpose == "manual"]

    day_counts = Counter(aware(i.created_at).astimezone().strftime("%m-%d") for i in incidents)
    series = []
    for d in range(days - 1, -1, -1):
        key = (utcnow() - timedelta(days=d)).astimezone().strftime("%m-%d")
        series.append({"day": key, "count": day_counts.get(key, 0)})

    return {
        "days": days,
        "incidents": len(incidents),
        "resolved": sum(1 for i in incidents if i.status == IncidentStatus.resolved),
        "escalated": sum(1 for i in incidents if i.escalated_at),
        "first_response_median_min": median(frt) if frt else None,
        "resolution_median_min": median(res) if res else None,
        "replies_sent": len(replies),
        "ai_adopted": len(adopted),
        "ai_auto": sum(1 for o in replies if o.sent_by == "auto"),
        "human_edited": len(edited),
        "manual_messages": len(manual),
        "by_category": Counter(i.category for i in incidents).most_common(),
        "by_responsible": Counter(i.responsible or "pending" for i in incidents).most_common(),
        "series": series,
        "waiting": await waiting_customers(session),
    }


async def waiting_customers(session: AsyncSession) -> list[dict]:
    """客户最后一条消息之后我们还没说过话的会话，按等待时长排序。"""
    active = (await session.scalars(select(Incident).where(
        Incident.status != IncidentStatus.resolved))).all()
    out = []
    for inc in active:
        msgs = (await session.scalars(select(Message).where(
            Message.incident_id == inc.id, Message.group_id == inc.group_id)
            .order_by(Message.id.desc()).limit(10))).all()
        last_customer = next((m for m in msgs if not m.from_us), None)
        last_ours = next((m for m in msgs if m.from_us), None)
        if last_customer is None or (last_ours and last_ours.id > last_customer.id):
            continue
        group = await session.get(Group, inc.group_id)
        out.append({"incident_id": inc.id, "group": group.name if group else "", "summary": inc.summary,
                    "last_message": last_customer.text, "waiting_min": _mins(last_customer.created_at, utcnow())})
    return sorted(out, key=lambda x: -x["waiting_min"])

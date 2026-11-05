"""SLA：客户等太久没人回 → 提醒负责人。工作时间外阈值放宽、只提醒值班的人。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Group, GroupKind, Incident, Message, User
from app.services import presend
from app.services.escalation import _accounts
from app.services.events import record
from app.services.stats import waiting_customers


def in_work_hours(now: datetime | None = None) -> bool:
    now = (now or presend.utcnow()).astimezone(ZoneInfo(settings.timezone))
    if now.isoweekday() not in settings.work_days:
        return False
    start, end = settings.work_hours.split("-")
    return start <= now.strftime("%H:%M") < end


async def check_sla(session: AsyncSession) -> int:
    working = in_work_hours()
    internal = await session.scalar(select(Group).where(Group.kind == GroupKind.internal).order_by(Group.id))
    alerted = 0
    for w in await waiting_customers(session):
        inc = await session.get(Incident, w["incident_id"])
        group = await session.get(Group, inc.group_id)
        limit = (group.sla_minutes or settings.sla_minutes) if working else settings.sla_off_hours_minutes
        if w["waiting_min"] < limit:
            continue
        last = await session.scalar(select(Message.id).where(
            Message.incident_id == inc.id, Message.group_id == inc.group_id, Message.from_us.is_(False))
            .order_by(Message.id.desc()))
        if inc.sla_alerted_message_id == last:
            continue
        inc.sla_alerted_message_id = last
        # 提醒谁：被指派人 + 群负责人；非工作时间只提醒值班的人
        names: list[str] = []
        if inc.assignee_id and (u := await session.get(User, inc.assignee_id)) and (working or u.on_duty):
            names.append(u.display_name)
        if working:
            names += [x for x in (group.owner, group.backup_owner) if x]
        else:
            names += [u.display_name for u in (await session.scalars(select(User).where(
                User.on_duty.is_(True), User.active.is_(True)))).all()]
        names = list(dict.fromkeys(names))
        text = (f"⏰ {group.name} 的客户已等待 {int(w['waiting_min'])} 分钟没人回复"
                f"{'（非工作时间）' if not working else ''}\n客户说：「{w['last_message'][:80]}」\n事件 #{inc.id}：{inc.summary}")
        if internal is not None:
            await presend.enqueue(session, internal, text, 100, "escalation", inc.id,
                                  mentions=await _accounts(session, internal.platform, names))
        record(session, inc.id, "sla", f"客户已等待 {int(w['waiting_min'])} 分钟（阈值 {limit}），已提醒 "
               f"{'、'.join(names) or '（无人可提醒）'}", actor="system")
        alerted += 1
    await session.commit()
    return alerted

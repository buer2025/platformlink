"""事件分配：群负责人优先；否则在值班的人里选手上未结事件最少的。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Incident, IncidentStatus, User
from app.services.events import record


async def auto_assign(session: AsyncSession, group: Group, incident: Incident) -> User | None:
    user = None
    if group.owner:
        user = await session.scalar(select(User).where(
            User.display_name == group.owner, User.active.is_(True)))
    how = "群负责人"
    if user is None:
        load = (select(Incident.assignee_id, func.count().label("n"))
                .where(Incident.status != IncidentStatus.resolved, Incident.assignee_id.is_not(None))
                .group_by(Incident.assignee_id).subquery())
        user = await session.scalar(
            select(User).outerjoin(load, load.c.assignee_id == User.id)
            .where(User.active.is_(True), User.on_duty.is_(True))
            .order_by(func.coalesce(load.c.n, 0), User.id).limit(1))
        how = "值班中、手上事件最少"
    if user is None:
        return None
    incident.assignee_id = user.id
    record(session, incident.id, "assigned", f"自动指派给 {user.display_name}（{how}）")
    return user

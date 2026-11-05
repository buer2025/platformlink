"""会话生命周期：空闲自动关闭 → 沉淀客户记忆 → 新会话带着记忆开始。"""

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm
from app.config import settings
from app.models import GroupMemory, Incident, IncidentEvent, IncidentStatus, Message
from app.services import presend
from app.services.events import record

log = logging.getLogger(__name__)


async def _transcript(session: AsyncSession, inc: Incident) -> str:
    msgs = (await session.scalars(select(Message).where(Message.incident_id == inc.id)
                                  .order_by(Message.id).limit(60))).all()
    lines = [f"模型 {inc.model or '-'}，类别 {inc.category}，责任方 {inc.responsible or '-'}",
             f"问题: {inc.summary}", f"结论依据: {inc.evidence[:500]}"]
    if inc.supplier_last_reply:
        lines.append(f"供应商最后回复: {inc.supplier_last_reply[:300]}")
    lines += [f"{'[我方]' if m.from_us else '[客户]'} {m.text[:300]}" for m in msgs]
    return "\n".join(lines)


async def remember(session: AsyncSession, inc: Incident) -> GroupMemory | None:
    """事件关闭时写一条客户记忆（同一事件只写一次）。"""
    if await session.scalar(select(GroupMemory.id).where(GroupMemory.incident_id == inc.id)):
        return None
    text = await llm.summarize_memory(await _transcript(session, inc))
    if not text:
        date = presend.aware(inc.created_at).astimezone().strftime("%m/%d")
        who = {"customer": "客户侧", "us": "我方", "supplier": "上游"}.get(inc.responsible, "原因待定")
        text = f"{date} {inc.model or ''} {inc.summary[:60]}（{who}）".strip()
    mem = GroupMemory(group_id=inc.group_id, incident_id=inc.id, text=text[:500])
    session.add(mem)
    record(session, inc.id, "memory_saved", f"沉淀客户记忆：{mem.text}")
    return mem


async def recall(session: AsyncSession, group_id: int, limit: int = 5) -> list[str]:
    rows = (await session.scalars(select(GroupMemory).where(GroupMemory.group_id == group_id)
                                  .order_by(GroupMemory.id.desc()).limit(limit))).all()
    return [m.text for m in rows]


async def close_idle(session: AsyncSession) -> int:
    """长时间没有动静的事件自动关闭并沉淀记忆。等待供应商的不关（走催办）。"""
    cutoff = presend.utcnow() - timedelta(hours=settings.incident_idle_hours)
    rows = (await session.scalars(select(Incident).where(
        Incident.status.in_([IncidentStatus.open, IncidentStatus.investigating])))).all()
    closed = 0
    for inc in rows:
        last = await session.scalar(select(func.max(IncidentEvent.created_at)).where(
            IncidentEvent.incident_id == inc.id))
        if presend.aware(last or inc.created_at) > cutoff:
            continue
        inc.status, inc.resolved_at = IncidentStatus.resolved, presend.utcnow()
        record(session, inc.id, "auto_closed",
               f"{settings.incident_idle_hours} 小时无新消息，会话自动关闭", actor="system")
        try:
            await remember(session, inc)
        except Exception:
            log.exception("remember failed")
        closed += 1
    await session.commit()
    return closed

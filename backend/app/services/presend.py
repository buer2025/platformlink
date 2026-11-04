"""预发机制：准确率评分 → 倒计时 → 管理员不操作则自动发送。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.im.registry import get_adapter
from app.models import AutomationMode, Group, Message, Outbound, OutboundStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def delay_for(score: int, group: Group, purpose: str) -> int | None:
    """返回自动发送前的倒计时秒数；None 表示必须人工确认。"""
    if group.automation == AutomationMode.observe:
        return None
    if purpose == "ack":  # 低风险安抚消息，直接发
        return 0 if group.automation == AutomationMode.auto else None
    if group.automation == AutomationMode.confirm:
        return None
    for min_score, seconds in settings.presend_tiers:
        if score >= min_score:
            return seconds + group.extra_delay_seconds
    return None


async def enqueue(
    session: AsyncSession,
    group: Group,
    text: str,
    score: int,
    purpose: str,
    incident_id: int | None = None,
    reply_to: str = "",
    mentions: list[str] | None = None,
) -> Outbound:
    delay = delay_for(score, group, purpose)
    item = Outbound(
        group_id=group.id,
        incident_id=incident_id,
        purpose=purpose,
        text=text,
        reply_to_external_id=reply_to,
        mentions=mentions or [],
        score=score,
        status=OutboundStatus.pending if delay is not None else OutboundStatus.needs_approval,
        send_at=utcnow() + timedelta(seconds=delay) if delay is not None else None,
    )
    session.add(item)
    await session.flush()
    return item


async def deliver(session: AsyncSession, item: Outbound) -> None:
    group = item.group or await session.get(Group, item.group_id)
    try:
        ext_id = await get_adapter(group.platform).send(
            group.external_id, item.text, item.reply_to_external_id, item.mentions
        )
    except Exception as e:  # noqa: BLE001 — 任何发送失败都记录到队列
        item.status = OutboundStatus.failed
        item.error = str(e)[:1000]
        return
    item.status = OutboundStatus.sent
    item.sent_at = utcnow()
    session.add(Message(group_id=group.id, external_id=ext_id, sender="platformlink",
                        text=item.text, reply_to_external_id=item.reply_to_external_id,
                        from_us=True))


async def flush_due(session: AsyncSession) -> int:
    """发送所有倒计时到期的预发消息，返回发送条数。"""
    rows = (await session.scalars(
        select(Outbound).where(Outbound.status == OutboundStatus.pending)
    )).all()
    due = [r for r in rows if r.send_at and aware(r.send_at) <= utcnow()]
    for item in due:
        await deliver(session, item)
    await session.commit()
    return len(due)

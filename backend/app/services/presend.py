"""预发机制：准确率评分 → 倒计时 → 管理员不操作则自动发送；供应商转交单超时催办。"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.im.registry import get_adapter
from app.models import (
    AutomationMode,
    Group,
    GroupKind,
    Incident,
    IncidentStatus,
    Message,
    Outbound,
    OutboundStatus,
)
from app.services.events import PURPOSE_NAMES, record
from app.services.style import style_of

log = logging.getLogger(__name__)
OPEN = (OutboundStatus.pending, OutboundStatus.needs_approval)
RETRYABLE = (*OPEN, OutboundStatus.failed)


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def delay_for(score: int, group: Group, purpose: str) -> int | None:
    """返回自动发送前的倒计时秒数；None 表示必须人工确认。"""
    if purpose == "escalation" and group.kind == GroupKind.internal:
        return 0  # 内部群里 @ 负责人，不需要审批
    if group.automation == AutomationMode.observe:
        return None
    if purpose in ("ack", "escalation", "greeting"):  # 低风险安抚 / 招呼，直接发
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
    force_confirm: bool = False,
) -> Outbound:
    score = max(0, min(100, score))
    delay = None if force_confirm and purpose != "ack" else delay_for(score, group, purpose)
    takeover = ""
    if incident_id and purpose not in ("manual", "escalation"):
        inc = await session.get(Incident, incident_id)
        if inc and inc.human_until and aware(inc.human_until) > utcnow():
            delay, takeover = None, f"，{inc.human_by or '同事'}正在人工处理"  # 人工接管期间 AI 不自动发
    item = Outbound(
        original_text=text if purpose != "manual" else "",
        group_id=group.id,
        incident_id=incident_id,
        purpose=purpose,
        text=text,
        reply_to_external_id=reply_to,
        mentions=list(mentions or []),
        score=score,
        status=OutboundStatus.pending if delay is not None else OutboundStatus.needs_approval,
        send_at=utcnow() + timedelta(seconds=delay) if delay is not None else None,
    )
    session.add(item)
    await session.flush()
    name = PURPOSE_NAMES.get(purpose, purpose)
    plan = ("需人工审批" if delay is None else ("立即发送" if delay == 0 else f"{delay} 秒后自动发送")) + takeover
    record(session, incident_id, "queued", f"{name}进入预发队列（评分 {score}，{plan}）→ {group.name}")
    return item


async def claim(session: AsyncSession, item_id: int, allowed=OPEN) -> bool:
    """原子地把消息标记为发送中；已被其他发送方认领或已处理时返回 False。"""
    result = await session.execute(
        update(Outbound)
        .where(Outbound.id == item_id, Outbound.status.in_(allowed))
        .values(status=OutboundStatus.sending, claimed_at=utcnow())
    )
    await session.commit()
    return result.rowcount == 1


async def deliver(session: AsyncSession, item: Outbound, allowed=OPEN) -> bool:
    """认领并发送一条消息。调用方无需再提交。"""
    if not await claim(session, item.id, allowed):
        return False
    await session.refresh(item)
    group = await session.get(Group, item.group_id)
    try:
        adapter = get_adapter(group.platform)
        if group.platform == "wecom":
            ext_id = await adapter.send(group.external_id, item.text, item.reply_to_external_id,
                                        item.mentions, webhook=group.bot_webhook)
        else:
            ext_id = await adapter.send(
                group.external_id, item.text, item.reply_to_external_id, item.mentions
            )
    except Exception as e:  # noqa: BLE001 — 任何发送失败都记录到队列
        log.warning("send outbound %s failed: %s", item.id, e)
        item.status = OutboundStatus.failed
        item.error = str(e)[:1000] or e.__class__.__name__
        record(session, item.incident_id, "send_failed",
               f"{PURPOSE_NAMES.get(item.purpose, item.purpose)}发送失败：{item.error}")
        await session.commit()
        return False
    item.status = OutboundStatus.sent
    item.sent_at = utcnow()
    if not item.sent_by:
        item.sent_by = "manual" if item.purpose == "manual" else ("auto" if allowed == OPEN else "approved")
    item.error = ""
    # 人工从后台发的消息记成那位同事（用于风格学习）；AI 发的记为 platformlink
    sender = f"后台·{item.edited_by}" if item.purpose == "manual" and item.edited_by else "platformlink"
    session.add(Message(group_id=group.id, external_id=ext_id, sender=sender,
                        text=item.text, reply_to_external_id=item.reply_to_external_id,
                        from_us=True, incident_id=item.incident_id))
    record(session, item.incident_id, "sent",
           f"{PURPOSE_NAMES.get(item.purpose, item.purpose)}已发送到 {group.name}：{item.text}",
           actor=item.edited_by or "agent")
    if item.incident_id and item.purpose == "supplier_ticket":
        incident = await session.get(Incident, item.incident_id)
        if incident and not incident.ticket_sent_at:
            incident.ticket_sent_at = item.sent_at
    await session.commit()
    return True


async def remind_suppliers(session: AsyncSession) -> int:
    """转交单发出后供应商长时间未回复 → 生成催办消息。"""
    rows = (await session.scalars(select(Incident).where(
        Incident.status == IncidentStatus.waiting_supplier,
        Incident.ticket_sent_at.is_not(None),
        Incident.supplier_replied_at.is_(None),
        Incident.reminders < settings.supplier_max_reminders,
    ))).all()
    count = 0
    for inc in rows:
        due = aware(inc.ticket_sent_at) + timedelta(
            minutes=settings.supplier_remind_minutes * (inc.reminders + 1))
        if due > utcnow() or not inc.supplier_group_id:
            continue
        supplier = await session.get(Group, inc.supplier_group_id)
        from app.agent import llm  # 避免循环导入

        text = await llm.compose(
            "客气地催一下供应商处理上面转交的问题，客户那边还在等",
            f"问题: {inc.summary}\n已转交 {settings.supplier_remind_minutes * (inc.reminders + 1)} 分钟，"
            f"这是第 {inc.reminders + 1} 次催", await style_of(session, supplier), "麻烦帮忙看下上面这个问题，客户那边还在等，辛苦～")
        ticket = await session.scalar(select(Message).where(
            Message.incident_id == inc.id, Message.group_id == supplier.id,
            Message.from_us.is_(True)).order_by(Message.id))
        await enqueue(session, supplier, text, 90, "supplier_remind", inc.id,
                      reply_to=ticket.external_id if ticket else "", mentions=supplier.contacts)
        inc.reminders += 1
        count += 1
    await session.commit()
    return count


async def flush_due(session: AsyncSession) -> int:
    """发送所有倒计时到期的预发消息，返回成功发送条数。"""
    rows = (await session.scalars(
        select(Outbound).where(Outbound.status == OutboundStatus.pending)
    )).all()
    now = utcnow()
    sent = 0
    for item in [r for r in rows if r.send_at and aware(r.send_at) <= now]:
        sent += await deliver(session, item)
    return sent


STUCK_AFTER = timedelta(minutes=5)


async def recover_stuck(session: AsyncSession) -> int:
    """发送中卡住超过 5 分钟（进程崩溃 / 重启）的消息标为失败，交给人工确认。

    只看认领时间，不会误伤其他进程正在发送的消息；多副本部署时也安全。
    """
    cutoff = utcnow() - STUCK_AFTER
    r = await session.execute(
        update(Outbound)
        .where(Outbound.status == OutboundStatus.sending,
               or_(Outbound.claimed_at.is_(None), Outbound.claimed_at < cutoff))
        .values(status=OutboundStatus.failed, error="发送过程中服务中断，请确认对方是否已收到后再重发")
    )
    await session.commit()
    return r.rowcount

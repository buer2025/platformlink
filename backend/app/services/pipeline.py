"""入站消息处理：存储 → 识别问题 → 排查 → 进入预发队列；供应商回复关联转交单。"""

import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm
from app.agent.logs import query_log
from app.config import settings
from app.im.base import InboundMessage
from app.models import (
    AutomationMode,
    Group,
    GroupKind,
    Incident,
    IncidentStatus,
    Message,
    Platform,
    Staff,
)
from app.services import presend

log = logging.getLogger(__name__)

RESOLVED_HINT = re.compile(r"(已?修复|已?恢复|好了|解决了|fixed|resolved|扩好|已扩)", re.IGNORECASE)


async def upsert_group(session: AsyncSession, m: InboundMessage) -> Group:
    group = await session.scalar(
        select(Group).where(Group.platform == m.platform, Group.external_id == m.chat_id)
    )
    if group is None:
        # 新群默认只观察，等待在后台打标记
        group = Group(platform=Platform(m.platform), external_id=m.chat_id,
                      name=m.chat_name or m.chat_id, automation=AutomationMode.observe,
                      models=[], contacts=[], style={})
        session.add(group)
        await session.flush()
    elif m.chat_name and group.name == group.external_id:
        group.name = m.chat_name
    return group


async def find_supplier_group(session: AsyncSession, model: str) -> Group | None:
    if not model:
        return None
    groups = (await session.scalars(select(Group).where(Group.kind == GroupKind.supplier))).all()
    for g in groups:
        if any(model.lower().startswith(x.lower()) for x in g.models):
            return g
    return None


async def _is_staff(session: AsyncSession, m: InboundMessage) -> bool:
    if m.is_bot:
        return True
    return await session.scalar(
        select(Staff.id).where(Staff.platform == m.platform, Staff.account == m.sender)
    ) is not None


async def _handle_supplier_reply(session: AsyncSession, group: Group, msg: Message) -> None:
    """供应商引用了我方消息 → 关联到转交单。"""
    if not msg.reply_to_external_id:
        return
    ours = await session.scalar(select(Message).where(
        Message.group_id == group.id, Message.external_id == msg.reply_to_external_id,
        Message.from_us.is_(True)))
    if not ours or not ours.incident_id:
        return
    incident = await session.get(Incident, ours.incident_id)
    if not incident or incident.status == IncidentStatus.resolved:
        return
    msg.incident_id = incident.id
    incident.supplier_replied_at = presend.utcnow()
    incident.supplier_last_reply = msg.text[:2000]
    if RESOLVED_HINT.search(msg.text):
        customer = await session.get(Group, incident.group_id)
        origin = await session.get(Message, incident.message_id)
        text = await llm.stylize("上游反馈已经处理好了，麻烦再试一下，有问题随时说～",
                                 customer.style, "customer_reply")
        # 供应商说修好，需人工复核或 10 分钟倒计时后通知客户
        await presend.enqueue(session, customer, text, 75, "customer_reply", incident.id,
                              reply_to=origin.external_id if origin else "")


async def _mergeable(session: AsyncSession, group: Group, category: str) -> Incident | None:
    since = presend.utcnow() - timedelta(minutes=settings.incident_merge_minutes)
    rows = (await session.scalars(select(Incident).where(
        Incident.group_id == group.id, Incident.category == category,
        Incident.status != IncidentStatus.resolved,
    ).order_by(Incident.id.desc()))).all()
    return next((i for i in rows if presend.aware(i.created_at) >= since), None)


async def handle_inbound(session: AsyncSession, m: InboundMessage) -> Incident | None:
    group = await upsert_group(session, m)
    if m.message_id and await session.scalar(select(Message.id).where(
            Message.group_id == group.id, Message.external_id == m.message_id)):
        return None  # 平台重推，已处理过

    staff = await _is_staff(session, m)
    msg = Message(group_id=group.id, external_id=m.message_id, sender=m.sender, text=m.text,
                  reply_to_external_id=m.reply_to_message_id, from_us=staff)
    session.add(msg)
    await session.flush()

    if staff or group.automation == AutomationMode.observe:
        await session.commit()
        return None
    if group.kind == GroupKind.supplier:
        await _handle_supplier_reply(session, group, msg)
        await session.commit()
        return None
    if group.kind != GroupKind.customer:
        await session.commit()
        return None

    c = await llm.classify(m.text)
    if not c.is_issue:
        await session.commit()
        return None

    if existing := await _mergeable(session, group, c.category):
        existing.related_messages += 1
        msg.incident_id = existing.id
        if c.request_id and not existing.request_id:
            existing.request_id = c.request_id
        await session.commit()
        return existing

    incident = Incident(group_id=group.id, message_id=msg.id, category=c.category, model=c.model,
                        request_id=c.request_id, summary=c.summary,
                        status=IncidentStatus.investigating)
    session.add(incident)
    await session.flush()
    msg.incident_id = incident.id

    ack = await llm.stylize("收到，正在排查，稍等～", group.style, "ack")
    await presend.enqueue(session, group, ack, 100, "ack", incident.id, reply_to=m.message_id)

    try:
        lr = await query_log(c.request_id) if c.request_id else None
    except Exception:
        log.exception("query_log failed")
        lr = None
    f = await llm.investigate(c, lr)
    incident.responsible, incident.evidence, incident.confidence = (
        f.responsible, f.evidence, f.confidence)

    reply = await llm.stylize(f.customer_reply, group.style, "customer_reply")
    await presend.enqueue(session, group, reply, f.confidence, "customer_reply", incident.id,
                          reply_to=m.message_id)

    if f.responsible == "supplier" and f.supplier_note:
        supplier = await find_supplier_group(session, c.model)
        if supplier:
            incident.supplier_group_id = supplier.id
            incident.status = IncidentStatus.waiting_supplier
            note = await llm.stylize(f.supplier_note, supplier.style, "supplier_ticket")
            await presend.enqueue(session, supplier, note, f.confidence, "supplier_ticket",
                                  incident.id, mentions=supplier.contacts)
        else:
            incident.evidence += "\n（未找到标记了该模型的供应商群，需人工转交）"

    await session.commit()
    return incident

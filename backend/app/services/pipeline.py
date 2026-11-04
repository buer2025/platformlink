"""入站消息处理：存储 → 识别问题 → 排查 → 进入预发队列。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm
from app.agent.logs import query_log
from app.im.base import InboundMessage
from app.models import Group, GroupKind, Incident, IncidentStatus, Message, Platform
from app.services import presend


async def upsert_group(session: AsyncSession, m: InboundMessage) -> Group:
    group = await session.scalar(
        select(Group).where(Group.platform == m.platform, Group.external_id == m.chat_id)
    )
    if group is None:
        # 新群默认为客户群、只观察，等待在后台打标记
        group = Group(platform=Platform(m.platform), external_id=m.chat_id,
                      name=m.chat_name or m.chat_id, automation="observe")
        session.add(group)
        await session.flush()
    return group


async def find_supplier_group(session: AsyncSession, model: str) -> Group | None:
    if not model:
        return None
    groups = (await session.scalars(select(Group).where(Group.kind == GroupKind.supplier))).all()
    for g in groups:
        if any(model.lower().startswith(x.lower()) for x in g.models):
            return g
    return None


async def handle_inbound(session: AsyncSession, m: InboundMessage) -> Incident | None:
    group = await upsert_group(session, m)
    msg = Message(group_id=group.id, external_id=m.message_id, sender=m.sender, text=m.text,
                  reply_to_external_id=m.reply_to_message_id)
    session.add(msg)
    await session.flush()

    if group.kind != GroupKind.customer or group.automation == "observe":
        await session.commit()
        return None

    c = await llm.classify(m.text)
    if not c.is_issue:
        await session.commit()
        return None

    incident = Incident(group_id=group.id, message_id=msg.id, category=c.category, model=c.model,
                        request_id=c.request_id, summary=c.summary,
                        status=IncidentStatus.investigating)
    session.add(incident)
    await session.flush()

    ack = await llm.stylize("收到，正在排查，稍等～", group.style, "ack")
    await presend.enqueue(session, group, ack, 100, "ack", incident.id, reply_to=m.message_id)

    log = await query_log(c.request_id) if c.request_id else None
    f = await llm.investigate(c, log)
    incident.responsible, incident.evidence, incident.confidence = (
        f.responsible, f.evidence, f.confidence)

    reply = await llm.stylize(f.customer_reply, group.style, "customer_reply")
    await presend.enqueue(session, group, reply, f.confidence, "customer_reply", incident.id,
                          reply_to=m.message_id)

    if f.responsible == "supplier" and f.supplier_note:
        supplier = await find_supplier_group(session, c.model)
        if supplier:
            note = await llm.stylize(f.supplier_note, supplier.style, "supplier_ticket")
            await presend.enqueue(session, supplier, note, f.confidence, "supplier_ticket",
                                  incident.id, mentions=supplier.contacts)

    await session.commit()
    return incident

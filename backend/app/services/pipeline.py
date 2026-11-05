"""入站消息处理：存储 → 识别问题 → 排查 → 进入预发队列；供应商回复关联转交单。"""

import logging
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import harness, llm, logs
from app.config import settings
from app.im.base import InboundMessage
from app.models import (
    AutomationMode,
    Group,
    GroupKind,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Message,
    Outbound,
    OutboundStatus,
    Platform,
    Staff,
    Workflow,
)
from app.services import knowledge, memory, presend
from app.services.escalation import escalate
from app.services.events import record

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


DEFAULT_LOOKUPS = ["request_id", "recent_errors", "channel"]


async def match_workflow(session: AsyncSession, group: Group, category: str,
                         text: str) -> Workflow | None:
    """按优先级找第一条命中的链路：类别或关键词任一命中，且群标识满足限定。"""
    rows = (await session.scalars(select(Workflow).where(Workflow.enabled.is_(True))
                                  .order_by(Workflow.priority, Workflow.id))).all()
    tags = set(group.tags or [])
    low = text.lower()
    for w in rows:
        if w.group_tags and not tags & set(w.group_tags):
            continue
        if category in (w.categories or []) or any(
                k and k.lower() in low for k in (w.keywords or [])):
            return w
    return None


async def find_supplier_group(session: AsyncSession, model: str,
                              channel_ids: list[int] | None = None) -> Group | None:
    groups = (await session.scalars(select(Group).where(Group.kind == GroupKind.supplier))).all()
    # 日志里定位到的渠道最准确，优先按渠道找
    for ch in channel_ids or []:
        for g in groups:
            if ch in (g.channel_ids or []):
                return g
    if not model:
        return None
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
    record(session, incident.id, "supplier_reply", f"{group.name} / {msg.sender}：{msg.text}",
           actor=msg.sender)
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


async def _active_incident(session: AsyncSession, group: Group) -> Incident | None:
    """群里最近仍在处理中的事件（用于承接不像新问题的追问）。"""
    since = presend.utcnow() - timedelta(hours=settings.conversation_hours)
    rows = (await session.scalars(select(Incident).where(
        Incident.group_id == group.id, Incident.status != IncidentStatus.resolved,
    ).order_by(Incident.id.desc()).limit(5))).all()
    return next((i for i in rows if presend.aware(i.created_at) >= since), None)


async def _mergeable(session: AsyncSession, group: Group, category: str) -> Incident | None:
    since = presend.utcnow() - timedelta(minutes=settings.incident_merge_minutes)
    rows = (await session.scalars(select(Incident).where(
        Incident.group_id == group.id, Incident.category == category,
        Incident.status != IncidentStatus.resolved,
    ).order_by(Incident.id.desc()))).all()
    return next((i for i in rows if presend.aware(i.created_at) >= since), None)


async def _conversation(session: AsyncSession, incident: Incident) -> tuple[str, str, int]:
    """该事件下客户的全部消息：(按时间拼好的文本, 最新一条的平台消息 ID, 最大本地 ID)。"""
    rows = (await session.scalars(select(Message).where(
        Message.incident_id == incident.id, Message.group_id == incident.group_id,
        Message.from_us.is_(False)).order_by(Message.id))).all()
    if not rows:
        return "", "", 0
    text = "\n".join(f"[{m.created_at:%H:%M:%S}] {m.sender}: {m.text}" for m in rows)
    return text, rows[-1].external_id, rows[-1].id


PROGRESS = {
    IncidentStatus.investigating: "排查中",
    IncidentStatus.waiting_supplier: "已转交上游供应商，等待处理",
    IncidentStatus.resolved: "已解决",
    IncidentStatus.open: "新建",
}


def _progress(incident: Incident) -> str:
    lines = [f"状态: {PROGRESS.get(incident.status, incident.status)}",
             f"责任方判断: {incident.responsible or '未定'}", f"依据: {incident.evidence[:600]}"]
    if incident.status == IncidentStatus.waiting_supplier:
        lines.append(f"供应商回复: {incident.supplier_last_reply or '暂未回复'}"
                     + (f"（已催 {incident.reminders} 次）" if incident.reminders else ""))
    return "\n".join(lines)


async def _investigate(session: AsyncSession, group: Group, incident: Incident,
                       c: llm.Classification, text: str, reply_to: str,
                       wf: Workflow | None) -> None:
    """查数据 → Agent 排查 → 客户回复 / 供应商转交进入预发队列。"""
    force_confirm = bool(wf and wf.automation == "confirm")
    conv, _, seen = await _conversation(session, incident)
    text = conv or text
    steps = wf.lookups if wf else DEFAULT_LOOKUPS
    ev = await logs.lookup(steps, c.request_id, group.api_users or [], c.model)
    incident.lookup_result = ev.text
    record(session, incident.id, "lookup", ev.text)

    kb = await knowledge.search(session, f"{text}\n{ev.text}", c.model)
    if kb:
        for k in kb:
            k.hits += 1
        record(session, incident.id, "knowledge",
               "参考知识：" + "；".join(f"#{k.id} {k.title}" for k in kb))
    instructions = wf.instructions if wf else ""
    preloaded = ev.text + (f"\n\n## 知识库命中\n{knowledge.as_prompt(kb)}" if kb else "")
    ctx = harness.Ctx(session=session, group=group, incident=incident, c=c, text=text,
                      memories=await memory.recall(session, group.id),
                      found=ev.found,
                      channel_ids=list(ev.channel_ids), knowledge_ids=[k.id for k in kb])
    f = await harness.run(ctx, instructions, preloaded)
    if f is None:  # 无 Key / 调用失败：单次判断或规则兜底
        f = await llm.investigate(c, ev.text, instructions, ev.found, knowledge.as_prompt(kb))
    incident.knowledge_ids = ctx.knowledge_ids
    ev.channel_ids = ctx.channel_ids
    incident.responsible, incident.evidence, incident.confidence = (
        f.responsible, f.evidence, f.confidence)
    record(session, incident.id, "finding",
           f"责任方 {f.responsible}，准确率 {f.confidence}：{f.evidence}"
           + (f"\n自检：{f.self_check}" if f.self_check else ""))

    if f.responsible == "unknown" or f.confidence < settings.escalate_below_confidence:
        await escalate(session, group, incident, f"Agent 无法确定原因（准确率 {f.confidence}）")
    reply = f.customer_reply
    conv2, latest, newest = await _conversation(session, incident)
    if newest > seen:  # 排查期间客户又发了消息 → 一次性回复全部
        reply = await llm.integrate(reply, conv2, _progress(incident))
        record(session, incident.id, "integrated", "排查期间客户追加了消息，回复已整合为一条")
    reply = await llm.stylize(reply, group.style, "customer_reply")
    await presend.enqueue(session, group, reply, f.confidence, "customer_reply", incident.id,
                          reply_to=latest or reply_to, force_confirm=force_confirm)

    route = wf.route_supplier if wf else True
    # 已经转交过供应商的不再重复转交
    if f.responsible == "supplier" and f.supplier_note and route and not incident.ticket_sent_at:
        supplier = await find_supplier_group(session, c.model, ev.channel_ids)
        if supplier:
            incident.supplier_group_id = supplier.id
            incident.status = IncidentStatus.waiting_supplier
            note = await llm.stylize(f.supplier_note, supplier.style, "supplier_ticket")
            await presend.enqueue(session, supplier, note, f.confidence, "supplier_ticket",
                                  incident.id, mentions=supplier.contacts,
                                  force_confirm=force_confirm)
        else:
            incident.evidence += "\n（未找到对应的供应商群，需人工转交）"
            record(session, incident.id, "no_supplier", "未找到对应的供应商群，需人工转交")
            await escalate(session, group, incident, "判断是供应商问题，但没有找到对应的供应商群")



async def _reinvestigate(session: AsyncSession, group: Group, incident: Incident,
                         c: llm.Classification, m: InboundMessage) -> None:
    """客户补充或更正了 request_id：撤回还没发出的回复，按新信息重新排查。"""
    old = incident.request_id
    incident.request_id = c.request_id
    if not incident.model and c.model:
        incident.model = c.model
    pending = (await session.scalars(select(Outbound).where(
        Outbound.incident_id == incident.id, Outbound.status.in_(presend.OPEN),
        Outbound.purpose.in_(["customer_reply", "supplier_ticket"])))).all()
    for o in pending:
        o.status = OutboundStatus.cancelled
    record(session, incident.id, "reinvestigate",
           f"客户{'更正' if old else '补充'}了 request_id：{old or '无'} → {c.request_id}"
           + (f"，撤回 {len(pending)} 条未发送的消息" if pending else "") + "，重新排查")
    wf = await session.get(Workflow, incident.workflow_id) if incident.workflow_id else None
    await _investigate(session, group, incident, c, m.text, m.message_id, wf)


async def _respond_followup(session: AsyncSession, group: Group, incident: Incident,
                           m: InboundMessage, quoted: str = "") -> None:
    """同一事件里客户又发了消息：未发出的回复改写成整合版；已发出的则针对新消息跟进。"""
    replies = (await session.scalars(select(Outbound).where(
        Outbound.incident_id == incident.id, Outbound.purpose == "customer_reply",
        Outbound.status != OutboundStatus.cancelled).order_by(Outbound.id.desc()))).all()
    if not incident.responsible:
        # 首次排查还没结束，结束时会自动整合这条
        record(session, incident.id, "queued_for_integration", "排查进行中，这条会合并进首次回复")
        return
    wf = await session.get(Workflow, incident.workflow_id) if incident.workflow_id else None
    force_confirm = bool(wf and wf.automation == "confirm")
    conv, latest, _ = await _conversation(session, incident)
    last = replies[0] if replies else None
    if llm._NO_REPLY.match(m.text.strip()):  # 「好的」「谢谢」：不打断待发回复，也不回复
        record(session, incident.id, "no_reply", f"「{m.text}」无需回复")
        return
    if last and last.status in presend.RETRYABLE:
        text = await llm.integrate(last.text, conv, _progress(incident))
        text = await llm.stylize(text, group.style, "customer_reply")
        last.status = OutboundStatus.cancelled
        await presend.enqueue(session, group, text, last.score, "customer_reply", incident.id,
                              reply_to=latest, force_confirm=force_confirm)
        record(session, incident.id, "integrated", "客户追加了消息，未发出的回复已改写为整合版")
        return
    # 已经回复过：判断这条是不是在继续追问，是才回复
    d = await llm.followup(conv, m.text, _progress(incident), quoted)
    if d.problem_persists and incident.status == IncidentStatus.resolved:
        incident.status = IncidentStatus.investigating
        incident.resolved_at = None
        incident.escalated_at = None  # 重新打开后允许再次升级
        record(session, incident.id, "reopened", f"客户反馈问题仍存在，事件重新打开：{d.reason}")
    if d.problem_persists:
        await escalate(session, group, incident, f"已回复过，客户反馈问题仍未解决：「{m.text}」")
    if not d.should_reply or not d.text:
        record(session, incident.id, "no_reply", f"「{m.text}」无需回复：{d.reason}")
        return
    text = await llm.stylize(d.text, group.style, "customer_reply")
    await presend.enqueue(session, group, text, min(incident.confidence, 85), "customer_reply",
                          incident.id, reply_to=m.message_id, force_confirm=force_confirm)
    record(session, incident.id, "followup", f"客户{'引用消息' if quoted else ''}追问，已起草跟进回复：{d.reason}")
    asked = await session.scalar(select(func.count()).select_from(IncidentEvent).where(
        IncidentEvent.incident_id == incident.id, IncidentEvent.kind == "followup"))
    if asked + 1 >= settings.escalate_after_followups:
        await escalate(session, group, incident, f"客户已追问 {asked + 1} 次")


async def _handle_quote(session: AsyncSession, group: Group, msg: Message, m: InboundMessage,
                        quoted: Message) -> Incident | None:
    """引用了某个事件里的消息：归到那个事件，判断是不是在继续追问。"""
    incident = await session.get(Incident, quoted.incident_id)
    if incident is None:
        await session.commit()
        return None
    msg.incident_id = incident.id
    incident.related_messages += 1
    who = "我方回复" if quoted.from_us else "客户之前的消息"
    record(session, incident.id, "quoted", f"{m.sender} 引用了{who}「{quoted.text[:80]}」：{m.text}",
           actor=m.sender)
    await session.commit()
    c = await llm.classify(f"{m.text}\n（引用了：「{quoted.text}」）")
    if c.request_id and c.request_id != incident.request_id and incident.responsible:
        if incident.status == IncidentStatus.resolved:
            incident.status, incident.resolved_at = IncidentStatus.investigating, None
            record(session, incident.id, "reopened", "客户引用旧消息并给出新的 request_id，事件重新打开")
        await _reinvestigate(session, group, incident, c, m)
    else:
        await _respond_followup(session, group, incident, m, quoted=quoted.text)
    await session.commit()
    return incident


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

    # 客户引用了某条消息：先看被引用的消息属于哪个事件
    quoted = None
    if msg.reply_to_external_id:
        quoted = await session.scalar(select(Message).where(
            Message.group_id == group.id, Message.external_id == msg.reply_to_external_id))
    if quoted is not None and quoted.incident_id:
        return await _handle_quote(session, group, msg, m, quoted)

    # 第 0 层（不调用 AI）：「好的」「谢谢」「👍」这类确认语，只记录不处理
    if quoted is None and llm._NO_REPLY.match(m.text.strip()):
        if active := await _active_incident(session, group):
            msg.incident_id = active.id
            active.related_messages += 1
            record(session, active.id, "no_reply", f"「{m.text}」无需回复", actor=m.sender)
        await session.commit()
        return None

    # 第 1 层：轻量分诊（低思考强度，判断是不是问题、抽取字段）
    c = await llm.classify(m.text if quoted is None else f"{m.text}\n（引用了：「{quoted.text}」）")
    if not c.is_issue:
        # 持续对话：群里有未解决的事件时，追问 / 催促 / 致谢都接着这个事件聊
        if active := await _active_incident(session, group):
            active.related_messages += 1
            msg.incident_id = active.id
            record(session, active.id, "merged", f"{m.sender}：{m.text}", actor=m.sender)
            await session.commit()
            await _respond_followup(session, group, active, m)
            await session.commit()
            return active
        await session.commit()
        return None

    if existing := await _mergeable(session, group, c.category):
        existing.related_messages += 1
        msg.incident_id = existing.id
        record(session, existing.id, "merged", f"{m.sender}：{m.text}", actor=m.sender)
        await session.commit()  # 先落库，让并发进来的排查能看到这条消息
        if c.request_id and c.request_id != existing.request_id:
            await _reinvestigate(session, group, existing, c, m)
        else:
            await _respond_followup(session, group, existing, m)
        await session.commit()
        return existing

    wf = await match_workflow(session, group, c.category, m.text)
    incident = Incident(group_id=group.id, message_id=msg.id, category=c.category, model=c.model,
                        request_id=c.request_id, summary=c.summary,
                        workflow_id=wf.id if wf else None,
                        status=IncidentStatus.investigating)
    session.add(incident)
    await session.flush()
    msg.incident_id = incident.id
    record(session, incident.id, "created", f"{m.sender}：{m.text}", actor=m.sender)
    record(session, incident.id, "classified",
           f"类别 {c.category}，模型 {c.model or '未知'}，request_id {c.request_id or '无'}；"
           f"链路：{wf.name if wf else '默认'}")

    ack = await llm.stylize("收到，正在排查，稍等～", group.style, "ack")
    await presend.enqueue(session, group, ack, 100, "ack", incident.id, reply_to=m.message_id)
    await session.commit()

    await _investigate(session, group, incident, c, m.text, m.message_id, wf)
    await session.commit()
    return incident

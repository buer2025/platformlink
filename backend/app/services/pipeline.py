"""入站消息处理：存储 → 识别问题 → 排查 → 进入预发队列；供应商回复关联转交单。"""

import asyncio
import logging
import re
from collections import defaultdict
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
from app.services import knowledge, lease, memory, presend
from app.services.assign import auto_assign
from app.services.escalation import escalate
from app.services.events import record
from app.services.style import style_of

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
        text = await llm.compose(
            "告诉客户上游已经处理好了，请他再试一下，有问题随时说",
            f"客户最初的问题: {incident.summary}\n供应商刚才的回复: {msg.text}",
            await style_of(session, customer), "上游反馈已经处理好了，麻烦再试一下，有问题随时说～")
        # 供应商说修好，需人工复核或 10 分钟倒计时后通知客户
        await presend.enqueue(session, customer, text, 75, "customer_reply", incident.id,
                              reply_to=origin.external_id if origin else "")


async def _active_incident(session: AsyncSession, group: Group) -> Incident | None:
    """群里最近仍在处理中的事件（用于承接不像新问题的追问）。"""
    since = presend.utcnow() - timedelta(hours=settings.conversation_hours)
    rows = (await session.scalars(select(Incident).where(Incident.group_id == group.id)
                                  .order_by(Incident.id.desc()).limit(5))).all()
    for i in rows:
        if i.status != IncidentStatus.resolved and presend.aware(i.created_at) >= since:
            return i
        # 刚解决不久：客户说「好了谢谢」「又不行了」都还算这段对话
        if i.status == IncidentStatus.resolved and i.resolved_at and \
                presend.aware(i.resolved_at) >= presend.utcnow() - timedelta(hours=2):
            return i
    return None


async def _mergeable(session: AsyncSession, group: Group, category: str) -> Incident | None:
    since = presend.utcnow() - timedelta(minutes=settings.incident_merge_minutes)
    rows = (await session.scalars(select(Incident).where(
        Incident.group_id == group.id, Incident.category == category,
        Incident.status != IncidentStatus.resolved,
    ).order_by(Incident.id.desc()))).all()
    return next((i for i in rows if presend.aware(i.created_at) >= since), None)


KEEP_RECENT = 12  # 最近这么多条客户原话始终逐字保留
COMPRESS_OVER = 24  # 超过这么多条（或 COMPRESS_CHARS 字）开始压缩更早的部分
COMPRESS_CHARS = 6000
RESUMMARIZE_EVERY = 10  # 摘要之后又攒了这么多条旧消息才重新压缩，避免每条消息都调一次模型


def _line(m: Message) -> str:
    return f"[{m.created_at:%m-%d %H:%M}] {m.sender}: {m.text}"


async def _conversation(session: AsyncSession, incident: Incident) -> tuple[str, str, int]:
    """该事件下客户的全部消息：(按时间拼好的文本, 最新一条的平台消息 ID, 最大本地 ID)。

    对话过长时把较早的部分压成摘要（存在事件上增量更新），只保留最近 KEEP_RECENT 条原话，
    让模型上下文始终可控，又不丢 request_id 之类的硬信息。
    """
    rows = (await session.scalars(select(Message).where(
        Message.incident_id == incident.id, Message.group_id == incident.group_id,
        Message.from_us.is_(False)).order_by(Message.id))).all()
    if not rows:
        return "", "", 0
    latest, newest = rows[-1].external_id, rows[-1].id
    total_chars = sum(len(m.text) for m in rows)
    if len(rows) <= COMPRESS_OVER and total_chars <= COMPRESS_CHARS:
        return "\n".join(_line(m) for m in rows), latest, newest

    recent, older = rows[-KEEP_RECENT:], rows[:-KEEP_RECENT]
    pending = [m for m in older if m.id > incident.conv_summary_upto]
    if pending and (not incident.conv_summary or len(pending) >= RESUMMARIZE_EVERY):
        summary = await llm.summarize_conversation(
            incident.conv_summary, "\n".join(_line(m) for m in pending))
        if summary:
            incident.conv_summary, incident.conv_summary_upto = summary, pending[-1].id
            record(session, incident.id, "compressed",
                   f"对话较长，已把较早的 {len(older)} 条消息压缩为摘要")
            pending = []
    parts = []
    if incident.conv_summary:
        parts.append(f"【更早对话的摘要】\n{incident.conv_summary}")
    else:  # AI 不可用：保留最开始的问题描述，中间省略
        head = older[:2]
        parts.append("\n".join(_line(m) for m in head))
        pending = older[2:][-4:]
        if len(older) > 2 + len(pending):
            parts.append(f"……（中间省略 {len(older) - 2 - len(pending)} 条）……")
    if pending:  # 摘要之后、最近原话之前还没压缩的消息，原样带上
        parts.append("\n".join(_line(m) for m in pending))
    parts.append("【最近的消息】\n" + "\n".join(_line(m) for m in recent))
    return "\n\n".join(parts), latest, newest


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
    reply = await llm.stylize(reply, await style_of(session, group), "customer_reply")
    await presend.enqueue(session, group, reply, f.confidence, "customer_reply", incident.id,
                          reply_to=latest or reply_to, force_confirm=force_confirm)

    route = wf.route_supplier if wf else True
    # 已经转交过供应商的不再重复转交
    if f.responsible == "supplier" and f.supplier_note and route and not incident.ticket_sent_at:
        supplier = await find_supplier_group(session, c.model, ev.channel_ids)
        if supplier:
            incident.supplier_group_id = supplier.id
            incident.status = IncidentStatus.waiting_supplier
            note = await llm.stylize(f.supplier_note, await style_of(session, supplier), "supplier_ticket")
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
    if last and last.status in presend.RETRYABLE and incident.status != IncidentStatus.resolved:
        text = await llm.integrate(last.text, conv, _progress(incident))
        text = await llm.stylize(text, await style_of(session, group), "customer_reply")
        last.status = OutboundStatus.cancelled
        await presend.enqueue(session, group, text, last.score, "customer_reply", incident.id,
                              reply_to=latest, force_confirm=force_confirm)
        record(session, incident.id, "integrated", "客户追加了消息，未发出的回复已改写为整合版")
        return
    # 已经回复过：判断这条是不是在继续追问，是才回复
    d = await llm.followup(conv, m.text, _progress(incident), quoted,
                           resolved=incident.status == IncidentStatus.resolved)
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
    text = await llm.stylize(d.text, await style_of(session, group), "customer_reply")
    await presend.enqueue(session, group, text, min(incident.confidence, 85), "customer_reply",
                          incident.id, reply_to=m.message_id, force_confirm=force_confirm)
    record(session, incident.id, "followup", f"客户{'引用消息' if quoted else ''}追问，已起草跟进回复：{d.reason}")
    asked = await session.scalar(select(func.count()).select_from(IncidentEvent).where(
        IncidentEvent.incident_id == incident.id, IncidentEvent.kind == "followup"))
    if asked + 1 >= settings.escalate_after_followups:
        await escalate(session, group, incident, f"客户已追问 {asked + 1} 次")


async def _human_takeover(session: AsyncSession, group: Group, msg: Message, who: str) -> None:
    """我方同事在群里亲自回复：AI 让位一段时间，倒计时中的 AI 消息改为等人工确认。"""
    incident = await _active_incident(session, group)
    if incident is None or incident.status == IncidentStatus.resolved:
        return
    msg.incident_id = incident.id
    staff_name = await session.scalar(select(Staff.name).where(
        Staff.platform == group.platform, Staff.account == who)) or who
    first = not (incident.human_until and presend.aware(incident.human_until) > presend.utcnow())
    incident.human_until = presend.utcnow() + timedelta(minutes=settings.human_takeover_minutes)
    incident.human_by = staff_name
    held = (await session.scalars(select(Outbound).where(
        Outbound.incident_id == incident.id, Outbound.group_id == group.id,
        Outbound.status == OutboundStatus.pending))).all()
    for o in held:
        o.status, o.send_at = OutboundStatus.needs_approval, None
    if first or held:
        record(session, incident.id, "human_takeover",
               f"{staff_name} 在群里亲自回复，AI 暂停自动发送 {settings.human_takeover_minutes} 分钟"
               + (f"，{len(held)} 条倒计时中的消息改为等你确认" if held else ""), actor=staff_name)


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


# 同一个群的消息严格按顺序处理：排查要十几秒，期间同群的新消息排队，
# 等前一条处理完再合并 / 整合，避免并发改写同一条回复。
# 进程内先用 asyncio.Lock 保证先来先处理，再用数据库租约挡住其他进程 / 副本。
_group_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)


async def handle_inbound(session: AsyncSession, m: InboundMessage) -> Incident | None:
    key = (m.platform, m.chat_id)
    async with _group_locks[key], lease.hold(f"group:{m.platform}:{m.chat_id}"):
        try:
            return await _handle_inbound(session, m)
        except Exception as e:
            log.exception("处理消息失败 %s/%s", m.platform, m.chat_id)
            await session.rollback()
            await _on_failure(session, m, e)
            return None


async def _on_failure(session: AsyncSession, m: InboundMessage, err: Exception) -> None:
    """处理出错时不能让客户石沉大海：记到事件上并 @ 负责人。"""
    try:
        group = await session.scalar(select(Group).where(
            Group.platform == m.platform, Group.external_id == m.chat_id))
        if group is None or group.kind != GroupKind.customer:
            return
        incident = await _active_incident(session, group)
        if incident is None:
            return
        record(session, incident.id, "error", f"处理「{m.text[:80]}」时出错：{type(err).__name__}", actor="system")
        incident.escalated_at = None
        await escalate(session, group, incident, f"系统处理客户消息出错（{type(err).__name__}），请人工跟进")
        await session.commit()
    except Exception:
        log.exception("failure fallback failed")
        await session.rollback()


async def _read_images(m: InboundMessage) -> None:
    """把截图里的文字（报错、request_id）并进消息正文，后续分诊 / 排查都能用上。"""
    from app.im.registry import get_adapter
    readings = []
    for image_id in m.image_ids[:3]:
        try:
            img = await get_adapter(m.platform).fetch_image(image_id)
        except Exception:
            log.exception("下载图片失败")
            continue
        if img and (txt := await llm.read_image(*img)):
            readings.append(txt)
    if readings:
        base = "" if m.text == "[图片]" else m.text + "\n"
        m.text = base + "\n".join(f"[截图内容] {r}" for r in readings)


async def _respond_chat(session: AsyncSession, group: Group, m: InboundMessage, kind: str) -> None:
    """没有进行中的问题，但客户在叫我们 / 问一般性问题：像真人一样接一句，而不是装没看见。

    招呼直接回；咨询类先查知识库，答不了就说去确认（倒计时期间人工可改），绝不编造。
    """
    style = await style_of(session, group)
    if kind == "greeting":
        text = await llm.compose(
            "客户在招呼/呼叫我们，回应说在的，请他直接说需要什么帮助。一句话，自然随意",
            f"客户刚发的消息: {m.text}", style, "在的，您说～")
        await presend.enqueue(session, group, text, 100, "greeting", reply_to=m.message_id)
        return
    hits = await knowledge.search(session, m.text, "")
    ref = "\n".join(f"- {k.title}：{k.solution}" for k in hits)
    text = await llm.compose(
        "回答客户的咨询。只能用「知识库」里有的信息回答；知识库没有答案时，说帮他确认一下稍后回复，"
        "不要编造产品能力、价格或时间",
        f"客户刚发的消息: {m.text}\n\n知识库:\n{ref or '（没有相关条目）'}", style,
        "我帮您确认一下，稍后回复您～")
    # 有知识库依据的回答 10 分钟倒计时；没有依据只是「去确认」，必须人工看一眼并跟进
    await presend.enqueue(session, group, text, 80 if hits else 60, "chat", reply_to=m.message_id)


async def _handle_inbound(session: AsyncSession, m: InboundMessage) -> Incident | None:
    if m.image_ids and not m.is_bot:
        await _read_images(m)
    group = await upsert_group(session, m)
    if m.message_id and await session.scalar(select(Message.id).where(
            Message.group_id == group.id, Message.external_id == m.message_id)):
        return None  # 平台重推，已处理过

    staff = await _is_staff(session, m)
    msg = Message(group_id=group.id, external_id=m.message_id, sender=m.sender, text=m.text,
                  reply_to_external_id=m.reply_to_message_id, from_us=staff)
    session.add(msg)
    await session.flush()

    if staff and not m.is_bot and group.kind == GroupKind.customer:
        await _human_takeover(session, group, msg, m.sender)
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
        if c.reply_kind in ("greeting", "question") and not m.is_bot:
            await _respond_chat(session, group, m, c.reply_kind)
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
    await auto_assign(session, group, incident)
    record(session, incident.id, "classified",
           f"类别 {c.category}，模型 {c.model or '未知'}，request_id {c.request_id or '无'}；"
           f"链路：{wf.name if wf else '默认'}")

    ack = await llm.compose(
        "先回应客户这条消息（共情他的着急），告诉他收到了、这就去查。不要猜原因，也不要说「还没头绪 / 还没看出原因」",
        f"客户刚发的消息: {m.text}", await style_of(session, group), "收到，正在排查，稍等～")
    await presend.enqueue(session, group, ack, 100, "ack", incident.id, reply_to=m.message_id)
    await session.commit()

    await _investigate(session, group, incident, c, m.text, m.message_id, wf)
    await session.commit()
    return incident

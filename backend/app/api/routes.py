import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm, logs
from app.auth import require_admin, verify_webhook
from app.db import SessionLocal, get_session
from app.im.base import InboundMessage
from app.im.registry import get_adapter
from app.models import (
    Group,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Knowledge,
    Message,
    Outbound,
    OutboundStatus,
    Party,
    Platform,
    Staff,
    Workflow,
)
from app.schemas import (
    GroupOut,
    GroupUpdate,
    IncidentEventOut,
    IncidentOut,
    IncidentUpdate,
    KnowledgeIn,
    KnowledgeOut,
    LookupTest,
    MessageOut,
    OutboundEdit,
    OutboundOut,
    PartyIn,
    PartyOut,
    SimulateIn,
    StaffIn,
    StaffOut,
    StylePreviewIn,
    WorkflowIn,
    WorkflowOut,
)
from app.services import knowledge, presend
from app.services.events import record
from app.services.pipeline import handle_inbound

admin = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
public = APIRouter(prefix="/api")
Session = Depends(get_session)


def _aware(obj, *fields):
    for f in fields:
        setattr(obj, f, presend.aware(getattr(obj, f)))
    return obj


@admin.get("/me")
async def me():
    return {"ok": True}


# ---------- 群与标记 ----------

@admin.get("/groups", response_model=list[GroupOut])
async def list_groups(s: AsyncSession = Session):
    return (await s.scalars(select(Group).order_by(Group.id))).all()


@admin.patch("/groups/{group_id}", response_model=GroupOut)
async def update_group(group_id: int, body: GroupUpdate, s: AsyncSession = Session):
    g = await s.get(Group, group_id)
    if not g:
        raise HTTPException(404, "群不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    await s.commit()
    return g


@admin.get("/groups/{group_id}/messages", response_model=list[MessageOut])
async def group_messages(group_id: int, s: AsyncSession = Session):
    q = select(Message).where(Message.group_id == group_id).order_by(Message.id.desc()).limit(200)
    return [_aware(m, "created_at") for m in (await s.scalars(q)).all()]


@admin.post("/groups/{group_id}/style/learn")
async def learn_group_style(group_id: int, s: AsyncSession = Session):
    """分析该群历史聊天，学习我方同事的说话风格，返回草稿供确认。"""
    g = await s.get(Group, group_id)
    if not g:
        raise HTTPException(404, "群不存在")
    rows = (await s.scalars(select(Message).where(Message.group_id == group_id)
                            .order_by(Message.id.desc()).limit(400))).all()
    rows.reverse()
    ours = [m.text for m in rows if m.from_us and m.sender != "platformlink"]
    theirs = [m.text for m in rows if not m.from_us]
    if len(ours) < 3:
        raise HTTPException(422, f"这个群里我方同事的发言只有 {len(ours)} 条，至少需要 3 条才能学习。"
                                 "先在群里正常聊一段时间，或手动填写风格。")
    p = await llm.learn_style(ours, theirs)
    if p is None:
        raise HTTPException(503, "AI 暂时不可用，请稍后再试")
    return {"profile": p.model_dump(), "based_on": {"ours": len(ours), "theirs": len(theirs)}}


@admin.post("/groups/{group_id}/style/preview")
async def preview_group_style(group_id: int, body: StylePreviewIn, s: AsyncSession = Session):
    g = await s.get(Group, group_id)
    if not g:
        raise HTTPException(404, "群不存在")
    style = body.style if body.style is not None else (g.style or {})
    return {"text": await llm.stylize(body.text, style, "customer_reply")}


@admin.get("/parties", response_model=list[PartyOut])
async def list_parties(s: AsyncSession = Session):
    return (await s.scalars(select(Party).order_by(Party.id))).all()


@admin.post("/parties", response_model=PartyOut)
async def create_party(body: PartyIn, s: AsyncSession = Session):
    p = Party(**body.model_dump())
    s.add(p)
    try:
        await s.commit()
    except IntegrityError:
        raise HTTPException(409, "主体名称已存在") from None
    return p


# ---------- 我方人员 ----------

@admin.get("/staff", response_model=list[StaffOut])
async def list_staff(s: AsyncSession = Session):
    return (await s.scalars(select(Staff).order_by(Staff.id))).all()


@admin.post("/staff", response_model=StaffOut)
async def create_staff(body: StaffIn, s: AsyncSession = Session):
    st = Staff(**body.model_dump())
    s.add(st)
    try:
        await s.commit()
    except IntegrityError:
        raise HTTPException(409, "该账号已存在") from None
    return st


@admin.delete("/staff/{staff_id}")
async def delete_staff(staff_id: int, s: AsyncSession = Session):
    st = await s.get(Staff, staff_id)
    if st:
        await s.delete(st)
        await s.commit()
    return {"ok": True}


# ---------- 事件 ----------

INCIDENT_TIMES = ("created_at", "ticket_sent_at", "supplier_replied_at", "resolved_at")


@admin.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(status: str = "", group_id: int | None = None,
                         s: AsyncSession = Session):
    q = select(Incident).order_by(Incident.id.desc()).limit(200)
    if status == "active":
        q = q.where(Incident.status != IncidentStatus.resolved)
    elif status:
        q = q.where(Incident.status == status)
    if group_id:
        q = q.where(Incident.group_id == group_id)
    rows = (await s.scalars(q)).all()
    return [_aware(i, *INCIDENT_TIMES) for i in rows]


@admin.patch("/incidents/{incident_id}", response_model=IncidentOut)
async def update_incident(incident_id: int, body: IncidentUpdate, s: AsyncSession = Session):
    i = await s.get(Incident, incident_id)
    if not i:
        raise HTTPException(404, "事件不存在")
    actor = body.actor or "管理员"
    changes = body.model_dump(exclude_unset=True, exclude={"note", "actor"})
    for k, v in changes.items():
        if getattr(i, k) != v:
            record(s, i.id, "manual", f"{k}: {getattr(i, k)} → {v}", actor=actor)
            setattr(i, k, v)
    if changes.get("status") == IncidentStatus.resolved and not i.resolved_at:
        i.resolved_at = presend.utcnow()
    elif "status" in changes and changes["status"] != IncidentStatus.resolved:
        i.resolved_at = None
    if body.note.strip():
        record(s, i.id, "note", body.note.strip(), actor=actor)
    await s.commit()
    await s.refresh(i)
    return _aware(i, *INCIDENT_TIMES)


@admin.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: int, s: AsyncSession = Session):
    i = await s.get(Incident, incident_id)
    if not i:
        raise HTTPException(404, "事件不存在")
    return _aware(i, *INCIDENT_TIMES)


@admin.get("/incidents/{incident_id}/events", response_model=list[IncidentEventOut])
async def incident_events(incident_id: int, s: AsyncSession = Session):
    q = select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(
        IncidentEvent.id)
    return [_aware(e, "created_at") for e in (await s.scalars(q)).all()]


@admin.get("/incidents/{incident_id}/messages", response_model=list[MessageOut])
async def incident_messages(incident_id: int, s: AsyncSession = Session):
    q = select(Message).where(Message.incident_id == incident_id).order_by(Message.id)
    return [_aware(m, "created_at") for m in (await s.scalars(q)).all()]


# ---------- 预发队列 ----------

def _out(o: Outbound) -> OutboundOut:
    out = OutboundOut.model_validate(o)
    out.group_name = o.group.name if o.group else ""
    out.send_at = presend.aware(o.send_at)
    out.created_at = presend.aware(o.created_at)
    return out


@admin.get("/outbound", response_model=list[OutboundOut])
async def list_outbound(status: OutboundStatus | None = None, s: AsyncSession = Session):
    q = select(Outbound).order_by(Outbound.id.desc()).limit(200)
    if status:
        q = q.where(Outbound.status == status)
    return [_out(o) for o in (await s.scalars(q)).all()]


async def _get(s: AsyncSession, outbound_id: int, allowed=presend.OPEN) -> Outbound:
    o = await s.get(Outbound, outbound_id)
    if not o:
        raise HTTPException(404, "消息不存在")
    if o.status not in allowed:
        raise HTTPException(409, f"消息状态为 {o.status}，不能再操作")
    return o


@admin.post("/outbound/{outbound_id}/send", response_model=OutboundOut)
async def send_now(outbound_id: int, s: AsyncSession = Session):
    o = await _get(s, outbound_id, presend.RETRYABLE)
    if not await presend.deliver(s, o, presend.RETRYABLE):
        await s.refresh(o)
        if o.status != OutboundStatus.failed:
            raise HTTPException(409, "消息已被发送或取消")
    await s.refresh(o)
    return _out(o)


@admin.post("/outbound/{outbound_id}/edit", response_model=OutboundOut)
async def edit(outbound_id: int, body: OutboundEdit, s: AsyncSession = Session):
    o = await _get(s, outbound_id, presend.RETRYABLE)
    if not body.text.strip():
        raise HTTPException(422, "内容不能为空")
    # 人工修改后不再自动发送，等待确认
    o.text, o.edited_by = body.text.strip(), body.editor
    o.status, o.send_at = OutboundStatus.needs_approval, None
    record(s, o.incident_id, "edited", f"预发消息被修改：{o.text}", actor=body.editor or "管理员")
    await s.commit()
    return _out(o)


@admin.post("/outbound/{outbound_id}/cancel", response_model=OutboundOut)
async def cancel(outbound_id: int, s: AsyncSession = Session):
    o = await _get(s, outbound_id, presend.RETRYABLE)
    o.status = OutboundStatus.cancelled
    record(s, o.incident_id, "cancelled", f"预发消息已取消：{o.text}", actor="管理员")
    await s.commit()
    return _out(o)


# ---------- 链路 ----------

@admin.get("/workflows", response_model=list[WorkflowOut])
async def list_workflows(s: AsyncSession = Session):
    return (await s.scalars(select(Workflow).order_by(Workflow.priority, Workflow.id))).all()


async def _save_workflow(s: AsyncSession, w: Workflow, body: WorkflowIn) -> Workflow:
    for k, v in body.model_dump().items():
        setattr(w, k, v)
    s.add(w)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "已有同名链路") from None
    await s.refresh(w)
    return w


@admin.post("/workflows", response_model=WorkflowOut)
async def create_workflow(body: WorkflowIn, s: AsyncSession = Session):
    return await _save_workflow(s, Workflow(), body)


@admin.put("/workflows/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(workflow_id: int, body: WorkflowIn, s: AsyncSession = Session):
    w = await s.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(404, "链路不存在")
    return await _save_workflow(s, w, body)


@admin.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: int, s: AsyncSession = Session):
    w = await s.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(404, "链路不存在")
    w.enabled = False  # 已有事件引用，停用而不是删除
    await s.commit()
    return {"ok": True}


@admin.post("/lookup/test")
async def lookup_test(body: LookupTest):
    """在后台试跑一次日志库查询，用于验证连接与群绑定。"""
    ev = await logs.lookup(body.lookups, body.request_id, body.api_users, body.model)
    return {"configured": logs.get_logdb() is not None, "found": ev.found, "text": ev.text,
            "channel_ids": ev.channel_ids}


# ---------- 知识沉淀 ----------

@admin.get("/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(q: str = "", model: str = "", s: AsyncSession = Session):
    if q or model:
        return [_aware(k, "created_at", "updated_at")
                for k in await knowledge.search(s, q, model, limit=20)]
    rows = (await s.scalars(select(Knowledge).order_by(Knowledge.hits.desc(),
                                                       Knowledge.id.desc()))).all()
    return [_aware(k, "created_at", "updated_at") for k in rows]


@admin.post("/knowledge", response_model=KnowledgeOut)
async def create_knowledge(body: KnowledgeIn, s: AsyncSession = Session):
    k = Knowledge(**body.model_dump())
    s.add(k)
    await s.flush()
    record(s, body.source_incident_id, "knowledge_saved", f"沉淀为知识 #{k.id} {k.title}",
           actor="管理员")
    await s.commit()
    await s.refresh(k)
    return _aware(k, "created_at", "updated_at")


@admin.put("/knowledge/{kid}", response_model=KnowledgeOut)
async def update_knowledge(kid: int, body: KnowledgeIn, s: AsyncSession = Session):
    k = await s.get(Knowledge, kid)
    if not k:
        raise HTTPException(404, "知识不存在")
    for f, v in body.model_dump().items():
        setattr(k, f, v)
    await s.commit()
    await s.refresh(k)
    return _aware(k, "created_at", "updated_at")


async def _timeline_text(s: AsyncSession, i: Incident) -> str:
    events = (await s.scalars(select(IncidentEvent).where(
        IncidentEvent.incident_id == i.id).order_by(IncidentEvent.id))).all()
    lines = [f"模型: {i.model}，类别: {i.category}，request_id: {i.request_id}",
             f"客户问题: {i.summary}", f"责任方判断: {i.responsible}", f"依据: {i.evidence}",
             f"日志查询: {i.lookup_result}"]
    lines += [f"[{e.kind}] {e.actor}: {e.text}" for e in events]
    return "\n".join(lines)


@admin.post("/incidents/{incident_id}/knowledge-draft", response_model=KnowledgeIn)
async def knowledge_draft(incident_id: int, s: AsyncSession = Session):
    """根据事件时间线生成知识草稿，管理员确认/修改后再保存。"""
    i = await s.get(Incident, incident_id)
    if not i:
        raise HTTPException(404, "事件不存在")
    d = await llm.distill(await _timeline_text(s, i))
    if d:
        return KnowledgeIn(**d.model_dump(), models=[i.model] if i.model else [],
                           source_incident_id=i.id)
    return KnowledgeIn(title=i.summary[:100] or f"事件 #{i.id}", symptoms=i.summary,
                       models=[i.model] if i.model else [], responsible=i.responsible,
                       root_cause=i.evidence, solution=i.supplier_last_reply,
                       source_incident_id=i.id)


@admin.post("/simulate", response_model=IncidentOut | None)
async def simulate(body: SimulateIn, s: AsyncSession = Session):
    """本地调试：模拟一条群消息（同步处理，直接返回事件）。"""
    m = InboundMessage(platform=body.platform, chat_id=body.chat_id, chat_name=body.chat_name,
                       message_id=uuid.uuid4().hex[:8], sender=body.sender, text=body.text)
    i = await handle_inbound(s, m)
    return _aware(i, *INCIDENT_TIMES) if i else None


# ---------- Webhook（平台回调，不走后台登录，按平台密钥校验） ----------

async def _process(m: InboundMessage) -> None:
    async with SessionLocal() as s:
        try:
            await handle_inbound(s, m)
        except IntegrityError:
            await s.rollback()  # 并发重推导致的重复消息


@public.post("/webhooks/{platform}")
async def webhook(platform: Platform, request: Request, bg: BackgroundTasks):
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid json") from None
    if not verify_webhook(platform, request, payload):
        raise HTTPException(403, "webhook 校验失败")
    if payload.get("type") == "url_verification":  # 飞书回调地址校验
        return {"challenge": payload.get("challenge")}
    m = get_adapter(platform).parse_webhook(payload)
    if m:
        # 先返回 200，避免平台超时重推；Agent 在后台处理
        bg.add_task(_process, m)
    return {"ok": True}

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin, verify_webhook
from app.db import SessionLocal, get_session
from app.im.base import InboundMessage
from app.im.registry import get_adapter
from app.models import (
    Group,
    Incident,
    Message,
    Outbound,
    OutboundStatus,
    Party,
    Platform,
    Staff,
)
from app.schemas import (
    GroupOut,
    GroupUpdate,
    IncidentOut,
    IncidentUpdate,
    MessageOut,
    OutboundEdit,
    OutboundOut,
    PartyIn,
    PartyOut,
    SimulateIn,
    StaffIn,
    StaffOut,
)
from app.services import presend
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

INCIDENT_TIMES = ("created_at", "ticket_sent_at", "supplier_replied_at")


@admin.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(s: AsyncSession = Session):
    rows = (await s.scalars(select(Incident).order_by(Incident.id.desc()).limit(200))).all()
    return [_aware(i, *INCIDENT_TIMES) for i in rows]


@admin.patch("/incidents/{incident_id}", response_model=IncidentOut)
async def update_incident(incident_id: int, body: IncidentUpdate, s: AsyncSession = Session):
    i = await s.get(Incident, incident_id)
    if not i:
        raise HTTPException(404, "事件不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(i, k, v)
    await s.commit()
    return _aware(i, *INCIDENT_TIMES)


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
    await s.commit()
    return _out(o)


@admin.post("/outbound/{outbound_id}/cancel", response_model=OutboundOut)
async def cancel(outbound_id: int, s: AsyncSession = Session):
    o = await _get(s, outbound_id, presend.RETRYABLE)
    o.status = OutboundStatus.cancelled
    await s.commit()
    return _out(o)


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

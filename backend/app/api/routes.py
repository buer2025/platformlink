import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.im.base import InboundMessage
from app.im.registry import get_adapter
from app.models import Group, Incident, Message, Outbound, OutboundStatus, Party, Platform
from app.schemas import (
    GroupOut,
    GroupUpdate,
    IncidentOut,
    MessageOut,
    OutboundEdit,
    OutboundOut,
    PartyIn,
    PartyOut,
    SimulateIn,
)
from app.services import presend
from app.services.pipeline import handle_inbound

router = APIRouter(prefix="/api")
Session = Depends(get_session)


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(s: AsyncSession = Session):
    return (await s.scalars(select(Group).order_by(Group.id))).all()


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def update_group(group_id: int, body: GroupUpdate, s: AsyncSession = Session):
    g = await s.get(Group, group_id)
    if not g:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    await s.commit()
    return g


@router.get("/groups/{group_id}/messages", response_model=list[MessageOut])
async def group_messages(group_id: int, s: AsyncSession = Session):
    q = select(Message).where(Message.group_id == group_id).order_by(Message.id.desc()).limit(200)
    return (await s.scalars(q)).all()


@router.get("/parties", response_model=list[PartyOut])
async def list_parties(s: AsyncSession = Session):
    return (await s.scalars(select(Party).order_by(Party.id))).all()


@router.post("/parties", response_model=PartyOut)
async def create_party(body: PartyIn, s: AsyncSession = Session):
    p = Party(**body.model_dump())
    s.add(p)
    await s.commit()
    return p


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(s: AsyncSession = Session):
    return (await s.scalars(select(Incident).order_by(Incident.id.desc()).limit(200))).all()


def _out(o: Outbound) -> OutboundOut:
    out = OutboundOut.model_validate(o)
    out.group_name = o.group.name if o.group else ""
    out.send_at = presend.aware(o.send_at)
    return out


@router.get("/outbound", response_model=list[OutboundOut])
async def list_outbound(status: OutboundStatus | None = None, s: AsyncSession = Session):
    q = select(Outbound).order_by(Outbound.id.desc()).limit(200)
    if status:
        q = q.where(Outbound.status == status)
    return [_out(o) for o in (await s.scalars(q)).all()]


async def _pending(s: AsyncSession, outbound_id: int) -> Outbound:
    o = await s.get(Outbound, outbound_id)
    if not o:
        raise HTTPException(404)
    if o.status not in (OutboundStatus.pending, OutboundStatus.needs_approval):
        raise HTTPException(409, f"消息状态为 {o.status}，不能再操作")
    return o


@router.post("/outbound/{outbound_id}/send", response_model=OutboundOut)
async def send_now(outbound_id: int, s: AsyncSession = Session):
    o = await _pending(s, outbound_id)
    await presend.deliver(s, o)
    await s.commit()
    return _out(o)


@router.post("/outbound/{outbound_id}/edit", response_model=OutboundOut)
async def edit(outbound_id: int, body: OutboundEdit, s: AsyncSession = Session):
    o = await _pending(s, outbound_id)
    # 人工修改后不再自动发送，等待确认
    o.text, o.edited_by = body.text, body.editor
    o.status, o.send_at = OutboundStatus.needs_approval, None
    await s.commit()
    return _out(o)


@router.post("/outbound/{outbound_id}/cancel", response_model=OutboundOut)
async def cancel(outbound_id: int, s: AsyncSession = Session):
    o = await _pending(s, outbound_id)
    o.status = OutboundStatus.cancelled
    await s.commit()
    return _out(o)


@router.post("/webhooks/{platform}")
async def webhook(platform: Platform, payload: dict, s: AsyncSession = Session):
    if payload.get("type") == "url_verification":  # 飞书回调地址校验
        return {"challenge": payload.get("challenge")}
    m = get_adapter(platform).parse_webhook(payload)
    if m:
        await handle_inbound(s, m)
    return {"ok": True}


@router.post("/simulate", response_model=IncidentOut | None)
async def simulate(body: SimulateIn, s: AsyncSession = Session):
    m = InboundMessage(platform=body.platform, chat_id=body.chat_id, chat_name=body.chat_name,
                       message_id=uuid.uuid4().hex[:8], sender=body.sender, text=body.text)
    return await handle_inbound(s, m)

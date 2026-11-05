"""模型调用留存的查询，以及事件处理进度（前端轮询，实时反映到哪一步）。"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.models import (
    Incident,
    IncidentEvent,
    IncidentStatus,
    Message,
    ModelCall,
    Outbound,
    OutboundStatus,
)
from app.services import trace
from app.services.presend import aware

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
Session = Depends(get_session)


class CallOut(BaseModel):
    id: str
    created_at: datetime
    finished_at: datetime | None
    incident_id: int | None
    group_id: int | None
    message_id: int | None
    kind: str
    step: int
    provider: str
    model: str
    status: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    error: str
    model_config = {"from_attributes": True}


class CallDetail(CallOut):
    request: str
    response: str


def _out(c: ModelCall) -> ModelCall:
    c.created_at, c.finished_at = aware(c.created_at), aware(c.finished_at)
    return c


def _for_incident(incident_id: int):
    msgs = select(Message.id).where(Message.incident_id == incident_id)
    return or_(ModelCall.incident_id == incident_id, ModelCall.message_id.in_(msgs))


@router.get("/model-calls", response_model=list[CallOut])
async def list_calls(s: AsyncSession = Session, provider: str = "", kind: str = "", status: str = "",
                     incident_id: int | None = None, group_id: int | None = None,
                     before: datetime | None = None, limit: int = Query(50, le=200)):
    q = select(ModelCall)
    if provider:
        q = q.where(ModelCall.provider == provider)
    if kind:
        q = q.where(ModelCall.kind == kind)
    if status:
        q = q.where(ModelCall.status == status)
    if incident_id is not None:
        q = q.where(_for_incident(incident_id))
    if group_id is not None:
        q = q.where(ModelCall.group_id == group_id)
    if before is not None:
        q = q.where(ModelCall.created_at < before)
    rows = (await s.scalars(q.order_by(ModelCall.created_at.desc()).limit(limit))).all()
    # 建事件之前的调用（分诊等）只记了触发消息，这里补上它后来归属的事件
    need = {c.message_id for c in rows if c.incident_id is None and c.message_id}
    if need:
        owner = dict((await s.execute(select(Message.id, Message.incident_id)
                                      .where(Message.id.in_(need)))).all())
        for c in rows:
            if c.incident_id is None and c.message_id:
                c.incident_id = owner.get(c.message_id)
    out = [_out(c) for c in rows]
    s.expunge_all()  # 补的归属只用于展示，不写回
    return out


@router.get("/model-calls/stats")
async def call_stats(s: AsyncSession = Session, hours: int = Query(24, le=24 * 30)):
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (await s.execute(
        select(ModelCall.provider, func.count(), func.sum(ModelCall.input_tokens),
               func.sum(ModelCall.output_tokens), func.avg(ModelCall.latency_ms),
               func.sum(case((ModelCall.status == "error", 1), else_=0)))
        .where(ModelCall.created_at >= since).group_by(ModelCall.provider))).all()
    return [{"provider": p, "calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
             "avg_latency_ms": int(lat or 0), "errors": int(e or 0)} for p, n, i, o, lat, e in rows]


@router.get("/model-calls/{call_id}", response_model=CallDetail)
async def get_call(call_id: str, s: AsyncSession = Session):
    c = await s.get(ModelCall, call_id)
    if c is None:
        raise HTTPException(404)
    return _out(c)


@router.get("/incidents/{incident_id}/model-calls", response_model=list[CallOut])
async def incident_calls(incident_id: int, s: AsyncSession = Session):
    rows = (await s.scalars(select(ModelCall).where(_for_incident(incident_id))
                            .order_by(ModelCall.created_at))).all()
    return [_out(c) for c in rows]


# ---------- 处理进度 ----------

KIND_NAMES = {
    "classify": "分诊", "followup": "跟进判断", "agent": "排查 Agent", "investigate": "排查",
    "integrate": "整合回复", "compose": "撰写消息", "stylize": "润色", "read_image": "识别截图",
    "summarize_conversation": "压缩对话", "summarize_memory": "沉淀记忆", "distill": "沉淀知识",
    "learn_style": "学习风格",
}
TO_CUSTOMER = ("ack", "customer_reply", "manual", "escalation", "greeting", "chat")


def _when(events: list[IncidentEvent], *kinds: str) -> datetime | None:
    for e in events:
        if e.kind in kinds:
            return aware(e.created_at)
    return None


@router.get("/incidents/{incident_id}/live")
async def incident_live(incident_id: int, s: AsyncSession = Session):
    """事件现在走到哪一步：阶段列表 + 正在进行的模型调用 + 最新动态。"""
    inc = await s.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404)
    events = (await s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                              .order_by(IncidentEvent.id))).all()
    outs = (await s.scalars(select(Outbound).where(Outbound.incident_id == incident_id)
                            .order_by(Outbound.id))).all()
    msg_ids = set((await s.scalars(select(Message.id).where(Message.incident_id == incident_id))).all())
    db_running = (await s.scalars(select(ModelCall).where(
        _for_incident(incident_id), ModelCall.status == "running"))).all()
    now = datetime.now(UTC)
    merged = {c.id: {"id": c.id, "kind": c.kind, "step": c.step, "provider": c.provider,
                     "model": c.model, "started_at": aware(c.created_at)} for c in db_running}
    for r in list(trace.RUNNING.values()):  # 本进程内还没落库的进行中调用
        if r["incident_id"] == incident_id or r["message_id"] in msg_ids:
            merged[r["id"]] = r
    # 超过 3 分钟还没结束的视为进程中断，不再显示为「正在进行」
    running = sorted((r for r in merged.values() if r["id"] in trace.RUNNING or
                      (now - r["started_at"]).total_seconds() < 180), key=lambda r: r["started_at"])
    kinds_running = {c["kind"] for c in running}

    stages: list[dict] = []

    def add(key: str, label: str, done_at: datetime | None, active: bool = False,
            detail: str = "") -> None:
        state = "done" if done_at else ("active" if active else "pending")
        stages.append({"key": key, "label": label, "state": state,
                       "at": done_at.isoformat() if done_at else None, "detail": detail})

    add("received", "收到消息", aware(inc.created_at))
    add("triage", "分诊", _when(events, "classified", "merged") or (
        aware(inc.created_at) if events else None), "classify" in kinds_running)

    tools = [e for e in events if e.kind == "tool"]
    finding = _when(events, "finding")
    detail = ""
    agent_calls = [c for c in running if c["kind"] in ("agent", "investigate")]
    if agent_calls:
        detail = f"模型思考中 · 第 {agent_calls[-1]['step'] or 1} 轮"
    elif tools and not finding:
        detail = f"已调用 {len(tools)} 个工具 · 最近 {tools[-1].text.split('(')[0]}"
    elif finding:
        detail = f"调用 {len(tools)} 个工具，准确率 {inc.confidence}"
    add("investigate", "排查", finding, bool(agent_calls or (tools and not finding))
        or (inc.status == IncidentStatus.investigating and not finding), detail)

    replies = [o for o in outs if o.group_id == inc.group_id and o.purpose in TO_CUSTOMER
               and o.purpose != "ack"]
    sent = next((o for o in replies if o.status == OutboundStatus.sent), None)
    waiting = next((o for o in reversed(replies) if o.status in (
        OutboundStatus.pending, OutboundStatus.needs_approval, OutboundStatus.sending)), None)
    rdetail = ""
    if waiting is not None:
        if waiting.status == OutboundStatus.needs_approval:
            rdetail = "等待人工确认"
        elif waiting.status == OutboundStatus.sending:
            rdetail = "发送中"
        elif waiting.send_at:
            secs = max(0, int((aware(waiting.send_at) - now).total_seconds()))
            rdetail = f"{secs} 秒后自动发送"
    elif sent is not None:
        rdetail = f"已发送（{'自动' if sent.sent_by == 'auto' else '人工确认' if sent.sent_by == 'approved' else '人工'}）"
    add("reply", "回复客户", aware(sent.sent_at) if sent else None,
        waiting is not None or bool(kinds_running & {"compose", "stylize", "integrate", "followup"}),
        rdetail)

    if inc.supplier_group_id or inc.responsible == "supplier":
        sdetail = ""
        if inc.supplier_replied_at:
            sdetail = (inc.supplier_last_reply or "")[:60]
        elif inc.ticket_sent_at:
            sdetail = "等待供应商回复" + (f"（已催 {inc.reminders} 次）" if inc.reminders else "")
        elif not inc.supplier_group_id:
            sdetail = "没有匹配的供应商群，需人工转交"
        add("supplier", "转交供应商", aware(inc.supplier_replied_at),
            bool(inc.ticket_sent_at) or inc.status == IncidentStatus.waiting_supplier, sdetail)

    add("resolved", "解决", aware(inc.resolved_at) if inc.status == IncidentStatus.resolved else None)

    if inc.status == IncidentStatus.resolved:  # 已解决：没走到的阶段标记为跳过
        for st in stages:
            if st["state"] != "done":
                st["state"] = "skipped"
    # 只允许一个「进行中」：取第一个未完成且有动静的阶段；都没动静就是第一个未完成的
    active = next((st for st in stages if st["state"] == "active"), None)
    for st in stages:
        if st["state"] == "active" and st is not active:
            st["state"] = "pending"
    if active is None and inc.status != IncidentStatus.resolved:
        nxt = next((st for st in stages if st["state"] == "pending"), None)
        if nxt is not None:
            nxt["state"] = "active"

    last = events[-1] if events else None
    return {
        "status": inc.status,
        "escalated": bool(inc.escalated_at),
        "stages": stages,
        "running": [{"id": c["id"], "kind": c["kind"], "label": KIND_NAMES.get(c["kind"], c["kind"]),
                     "provider": c["provider"], "model": c["model"], "step": c["step"],
                     "started_at": c["started_at"].isoformat()} for c in running],
        "latest": {"kind": last.kind, "text": last.text[:200],
                   "at": aware(last.created_at).isoformat()} if last else None,
        "event_count": len(events),
    }

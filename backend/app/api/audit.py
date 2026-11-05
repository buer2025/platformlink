"""操作审计与数据库查询耗时的查询接口。审计只有管理员能看；事件里的日志查询所有登录用户可看。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_only, require_admin
from app.db import get_session
from app.models import AuditLog, DbQuery
from app.services.presend import aware, utcnow

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
Session = Depends(get_session)
AdminOnly = Depends(admin_only)


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    trace_id: str
    source: str
    actor: str
    actor_id: int | None
    role: str
    action: str
    method: str
    path: str
    target: str
    status: int
    ok: bool
    latency_ms: int
    db_count: int
    db_ms: int
    ip: str
    incident_id: int | None
    group_id: int | None
    model_config = {"from_attributes": True}


class QueryOut(BaseModel):
    id: int
    created_at: datetime
    trace_id: str
    db: str
    op: str
    caller: str
    statement: str
    params: str
    duration_ms: float
    rows: int
    error: str
    incident_id: int | None
    group_id: int | None
    model_config = {"from_attributes": True}


class AuditDetail(AuditOut):
    user_agent: str
    request: str
    detail: str
    queries: list[QueryOut] = []


def _aw(x):
    x.created_at = aware(x.created_at)
    return x


@router.get("/audit", response_model=list[AuditOut], dependencies=[AdminOnly])
async def list_audit(s: AsyncSession = Session, source: str = "", actor: str = "", q: str = "",
                     ok: bool | None = None, incident_id: int | None = None,
                     before_id: int | None = None, limit: int = Query(50, le=200)):
    stmt = select(AuditLog)
    if source:
        stmt = stmt.where(AuditLog.source == source)
    if actor:
        stmt = stmt.where(AuditLog.actor.contains(actor))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.action.like(like), AuditLog.path.like(like),
                              AuditLog.target.like(like), AuditLog.detail.like(like)))
    if ok is not None:
        stmt = stmt.where(AuditLog.ok.is_(ok))
    if incident_id is not None:
        stmt = stmt.where(AuditLog.incident_id == incident_id)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = (await s.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit))).all()
    return [_aw(r) for r in rows]


@router.get("/audit/stats", dependencies=[AdminOnly])
async def audit_stats(s: AsyncSession = Session, hours: int = Query(24, le=24 * 90)):
    since = utcnow() - timedelta(hours=hours)
    rows = (await s.execute(
        select(AuditLog.source, func.count(), func.sum(case((AuditLog.ok.is_(False), 1), else_=0)),
               func.count(func.distinct(AuditLog.actor)))
        .where(AuditLog.created_at >= since).group_by(AuditLog.source))).all()
    return [{"source": r[0], "count": r[1], "failed": int(r[2] or 0), "actors": r[3]} for r in rows]


@router.get("/audit/{audit_id}", response_model=AuditDetail, dependencies=[AdminOnly])
async def get_audit(audit_id: int, s: AsyncSession = Session):
    a = await s.get(AuditLog, audit_id)
    if a is None:
        raise HTTPException(404)
    queries = (await s.scalars(select(DbQuery).where(DbQuery.trace_id == a.trace_id)
                               .order_by(DbQuery.id).limit(500))).all()
    out = AuditDetail.model_validate(_aw(a))
    out.queries = [QueryOut.model_validate(_aw(x)) for x in queries]
    return out


@router.get("/db-queries", response_model=list[QueryOut], dependencies=[AdminOnly])
async def list_queries(s: AsyncSession = Session, db: str = "", min_ms: float = 0, caller: str = "",
                       errors: bool = False, incident_id: int | None = None, trace_id: str = "",
                       before_id: int | None = None, limit: int = Query(50, le=200)):
    stmt = select(DbQuery)
    if db:
        stmt = stmt.where(DbQuery.db == db)
    if min_ms:
        stmt = stmt.where(DbQuery.duration_ms >= min_ms)
    if caller:
        stmt = stmt.where(DbQuery.caller.contains(caller))
    if errors:
        stmt = stmt.where(DbQuery.error != "")
    if incident_id is not None:
        stmt = stmt.where(DbQuery.incident_id == incident_id)
    if trace_id:
        stmt = stmt.where(DbQuery.trace_id == trace_id)
    if before_id is not None:
        stmt = stmt.where(DbQuery.id < before_id)
    rows = (await s.scalars(stmt.order_by(DbQuery.id.desc()).limit(limit))).all()
    return [_aw(r) for r in rows]


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1)


@router.get("/db-queries/stats", dependencies=[AdminOnly])
async def query_stats(s: AsyncSession = Session, hours: int = Query(24, le=24 * 90)):
    """按库汇总：次数、平均 / P50 / P95 / 最慢、出错数；以及最耗时的调用方。
    分位数在最近 5000 条上计算（足够准，也不会拖慢接口）。"""
    since = utcnow() - timedelta(hours=hours)
    rows = (await s.execute(select(DbQuery.db, DbQuery.caller, DbQuery.duration_ms, DbQuery.error)
                            .where(DbQuery.created_at >= since)
                            .order_by(DbQuery.id.desc()).limit(5000))).all()
    by_db: dict[str, list] = {}
    by_caller: dict[tuple[str, str], list[float]] = {}
    for db, caller, ms, err in rows:
        by_db.setdefault(db, []).append((ms, bool(err)))
        by_caller.setdefault((db, caller or "（未知）"), []).append(ms)
    dbs = []
    for db, xs in sorted(by_db.items()):
        ms = [x for x, _ in xs]
        dbs.append({"db": db, "count": len(ms), "errors": sum(e for _, e in xs),
                    "avg_ms": round(sum(ms) / len(ms), 1), "p50_ms": _pct(ms, 0.5),
                    "p95_ms": _pct(ms, 0.95), "max_ms": round(max(ms), 1)})
    callers = sorted(({"db": db, "caller": c, "count": len(ms), "total_ms": round(sum(ms), 1),
                       "avg_ms": round(sum(ms) / len(ms), 1), "max_ms": round(max(ms), 1)}
                      for (db, c), ms in by_caller.items()), key=lambda x: -x["total_ms"])[:10]
    return {"dbs": dbs, "callers": callers, "sampled": len(rows)}


@router.get("/incidents/{incident_id}/db-queries", response_model=list[QueryOut])
async def incident_queries(incident_id: int, s: AsyncSession = Session):
    """排查这个事件时查了哪些日志、各花了多久（技术支持也能看）。"""
    rows = (await s.scalars(select(DbQuery).where(DbQuery.incident_id == incident_id)
                            .order_by(DbQuery.id).limit(500))).all()
    return [_aw(r) for r in rows]

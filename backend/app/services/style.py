"""群风格 = 管理员打的标签 + 从同事修改 AI 草稿中学到的改法。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, Outbound, OutboundStatus


async def corrections(session: AsyncSession, group_id: int, limit: int = 4) -> list[dict]:
    """最近几次同事把 AI 原稿改成了什么（已发出的才算）。"""
    rows = (await session.scalars(select(Outbound).where(
        Outbound.group_id == group_id, Outbound.status == OutboundStatus.sent,
        Outbound.original_text != "", Outbound.purpose != "manual",
    ).order_by(Outbound.id.desc()).limit(limit * 3))).all()
    out = [{"ai": o.original_text, "human": o.text} for o in rows
           if o.original_text.strip() != o.text.strip()]
    return out[:limit]


async def style_of(session: AsyncSession, group: Group) -> dict:
    st = dict(group.style or {})
    if fixes := await corrections(session, group.id):
        st["corrections"] = fixes
    return st

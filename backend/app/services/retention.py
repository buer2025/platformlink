"""过期记录清理：审计、数据库查询、模型调用超过 RECORD_RETENTION_DAYS 天的删除（0 = 永久保留）。"""

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditLog, DbQuery, ModelCall
from app.services.presend import utcnow


async def purge(session: AsyncSession) -> int:
    days = settings.record_retention_days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    n = 0
    for model in (AuditLog, DbQuery, ModelCall):
        r = await session.execute(delete(model).where(model.created_at < cutoff))
        n += r.rowcount or 0
    await session.commit()
    return n

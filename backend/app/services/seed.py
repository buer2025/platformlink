"""首次启动时写入默认链路，管理员可在后台修改或停用。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Workflow

DEFAULTS = [
    {
        "name": "模型调用问题", "priority": 10, "categories": ["error", "rate_limit"],
        "keywords": ["报错", "429", "超时", "timeout", "500", "502"],
        "lookups": ["request_id", "recent_errors", "channel"],
        "description": "客户反馈调用报错 / 429 / 超时：查日志定位渠道，判断责任方，必要时转交供应商。",
        "instructions": (
            "1. 有 request_id 先看该请求的日志：type=5 为错误，content/other 中有上游报错原文。\n"
            "2. 错误为参数错误、余额不足、令牌无效、内容审核 → 客户侧，直接说明原因与解决办法。\n"
            "3. 上游返回 429/5xx/超时，或同一渠道近期集中报错 → 供应商侧，转交对应供应商群。\n"
            "4. 没有 request_id 且近期无错误记录 → 礼貌索要 request_id 或报错截图。"),
    },
    {
        "name": "模型用量需求", "priority": 20, "categories": ["usage_request"],
        "keywords": ["扩容", "加量", "提额", "并发", "RPM", "TPM"],
        "lookups": ["usage"], "automation": "confirm",
        "description": "客户提出加量 / 扩容 / 开通需求：查近 24 小时用量，整理需求后找供应商要量。",
        "instructions": (
            "1. 根据用量数据说明客户当前量级（调用次数、错误率、tokens）。\n"
            "2. 给客户的回复：确认收到需求，询问目标量级（RPM/TPM/日调用量）与时间点，不承诺具体额度。\n"
            "3. 给供应商的转交：带上当前量级和客户的目标量，语气客气，像老朋友沟通，不要像机器通知。"),
    },
]


async def seed_defaults(session: AsyncSession) -> None:
    if await session.scalar(select(func.count()).select_from(Workflow)):
        return
    base = {"description": "", "enabled": True, "priority": 100, "categories": [], "keywords": [],
            "group_tags": [], "lookups": [], "instructions": "", "route_supplier": True,
            "automation": ""}
    for w in DEFAULTS:
        session.add(Workflow(**{**base, **w}))
    await session.commit()

"""知识检索：按模型、关键词、错误码与现象文本打分，取最相关的几条经验给 Agent。

不依赖向量库，几百上千条经验内足够快且可解释；条目量大后可换成 pg 全文检索 / 向量检索。
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Knowledge

TOKEN = re.compile(r"[A-Za-z][\w.-]{2,}|\d{3}|[一-鿿]{2,}")
STOP = {"request_id", "request", "报错了", "一直", "这个", "我们", "你们", "客户"}


def tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN.findall(text or "")} - STOP


def score(k: Knowledge, text: str, model: str, toks: set[str]) -> float:
    s = 0.0
    low = text.lower()
    if model and any(model.lower().startswith(m.lower()) for m in k.models or []):
        s += 3
    elif k.models and model:
        s -= 1  # 明确属于其他模型的经验降权
    s += 2 * sum(1 for kw in k.keywords or [] if kw and kw.lower() in low)
    q = text.strip().lower()
    if q and len(q) <= 40 and q in f"{k.title}\n{k.symptoms}".lower():  # 搜索框短查询：直接子串命中
        s += 3
    overlap = toks & tokens(k.symptoms + " " + k.title)
    s += len(overlap) * 0.5
    return s


async def search(session: AsyncSession, text: str, model: str, limit: int = 3) -> list[Knowledge]:
    rows = (await session.scalars(select(Knowledge).where(Knowledge.enabled.is_(True)))).all()
    toks = tokens(text)
    ranked = sorted(((score(k, text, model, toks), k) for k in rows), key=lambda x: -x[0])
    return [k for sc, k in ranked if sc >= 2][:limit]


def as_prompt(items: list[Knowledge]) -> str:
    if not items:
        return ""
    out = []
    for k in items:
        out.append(f"#{k.id} {k.title}\n  现象: {k.symptoms}\n  责任方: {k.responsible}\n"
                   f"  原因: {k.root_cause}\n  处理: {k.solution}"
                   + (f"\n  回复参考: {k.reply_template}" if k.reply_template else ""))
    return "\n".join(out)

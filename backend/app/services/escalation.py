"""升级人工：Agent 处理不了时 @ 公司内部负责人。

去哪里 @：
- 有内部群（群类型 = 内部群）→ 发到内部群，@ 客户群的负责人 / 备份负责人，带上事件摘要和原因；
- 没有内部群 → 在客户群里自然地说一句「拉负责同事一起看」，并 @ 负责人。
负责人填我方人员的姓名（自动换成对应平台账号）或直接填平台账号。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm
from app.models import Group, GroupKind, Incident, Staff, User
from app.services import presend
from app.services.events import record
from app.services.style import style_of


async def _accounts(session: AsyncSession, platform, names: list[str]) -> list[str]:
    out = []
    for n in names:
        acc = await session.scalar(select(Staff.account).where(
            Staff.platform == platform, Staff.name == n))
        out.append(acc or n)
    return list(dict.fromkeys(out))


async def escalate(session: AsyncSession, group: Group, incident: Incident, reason: str) -> bool:
    """同一事件只升级一次（重新打开后可再次升级）。返回是否发起了升级。"""
    if incident.escalated_at:
        return False
    incident.escalated_at = presend.utcnow()
    incident.escalation_reason = reason
    assignee = await session.get(User, incident.assignee_id) if incident.assignee_id else None
    owners = list(dict.fromkeys(x for x in (assignee and assignee.display_name, group.owner,
                                            group.backup_owner) if x))
    internal = await session.scalar(select(Group).where(Group.kind == GroupKind.internal)
                                    .order_by(Group.id))
    if internal is not None:
        text = (f"【需要人工】{group.name} · 事件 #{incident.id}\n{incident.summary}\n"
                f"原因：{reason}" + ("" if owners else "\n（这个客户群还没设置负责人，请认领）"))
        await presend.enqueue(session, internal, text, 100, "escalation", incident.id,
                              mentions=await _accounts(session, internal.platform, owners))
        record(session, incident.id, "escalated", f"已在内部群「{internal.name}」@ "
               f"{'、'.join(owners) or '（未设置负责人）'}：{reason}")
    elif owners:
        text = await llm.compose("告诉客户我拉负责的同事一起来看这个问题", f"问题: {incident.summary}",
                                 await style_of(session, group), "这个问题我拉一下负责的同事一起看下，稍等哈")
        await presend.enqueue(session, group, text, 100, "escalation", incident.id,
                              mentions=await _accounts(session, group.platform, owners))
        record(session, incident.id, "escalated", f"在客户群 @ {'、'.join(owners)}：{reason}")
    else:
        record(session, incident.id, "escalated",
               f"需要人工处理，但没有内部群、该群也没设置负责人，请在后台跟进：{reason}")
    return True

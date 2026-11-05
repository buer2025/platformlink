"""事件时间线记录。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IncidentEvent

PURPOSE_NAMES = {
    "ack": "安抚回复", "customer_reply": "客户回复", "supplier_ticket": "供应商转交单",
    "supplier_remind": "供应商催办", "quota_push": "催量",
}


def record(session: AsyncSession, incident_id: int | None, kind: str, text: str = "",
           actor: str = "agent") -> None:
    if incident_id:
        session.add(IncidentEvent(incident_id=incident_id, kind=kind, text=text[:4000],
                                  actor=actor))

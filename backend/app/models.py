import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now() -> datetime:
    return datetime.now(UTC)


class Platform(enum.StrEnum):
    telegram = "telegram"
    feishu = "feishu"
    wecom = "wecom"


class GroupKind(enum.StrEnum):
    customer = "customer"
    supplier = "supplier"
    internal = "internal"


class AutomationMode(enum.StrEnum):
    auto = "auto"  # 按预发分档自动发送
    confirm = "confirm"  # 必须人工确认
    observe = "observe"  # 只观察不发言


class Party(Base):
    """群背后的公司：客户或供应商。"""

    __tablename__ = "parties"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[GroupKind] = mapped_column(Enum(GroupKind))
    groups: Mapped[list["Group"]] = relationship(back_populates="party")


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[GroupKind] = mapped_column(Enum(GroupKind), default=GroupKind.customer)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    party: Mapped[Party | None] = relationship(back_populates="groups", lazy="selectin")

    # 业务标记：models / level / contacts（要 @ 的对接人）
    models: Mapped[list[str]] = mapped_column(JSON, default=list)
    level: Mapped[str] = mapped_column(String(50), default="")
    contacts: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 风格标记：{"address": "X总", "tone": "熟络", "phrases": [...], "taboos": [...], "samples": [...]}
    style: Mapped[dict] = mapped_column(JSON, default=dict)
    # 策略标记
    automation: Mapped[AutomationMode] = mapped_column(
        Enum(AutomationMode), default=AutomationMode.auto
    )
    extra_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(100), default="")
    backup_owner: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200), default="")
    sender: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text)
    reply_to_external_id: Mapped[str] = mapped_column(String(200), default="")
    from_us: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IncidentStatus(enum.StrEnum):
    open = "open"
    investigating = "investigating"
    resolved = "resolved"


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    category: Mapped[str] = mapped_column(String(50))  # error / rate_limit / other
    model: Mapped[str] = mapped_column(String(100), default="")
    request_id: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    responsible: Mapped[str] = mapped_column(String(20), default="")  # customer/us/supplier
    evidence: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus), default=IncidentStatus.open
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutboundStatus(enum.StrEnum):
    pending = "pending"  # 预发中，等待倒计时或人工
    needs_approval = "needs_approval"  # 分数太低，必须人工
    sent = "sent"
    cancelled = "cancelled"
    failed = "failed"


class Outbound(Base):
    """预发队列中的一条对外消息。"""

    __tablename__ = "outbound"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    group: Mapped[Group] = relationship(lazy="selectin")
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    purpose: Mapped[str] = mapped_column(String(50))  # ack / customer_reply / supplier_ticket / quota_push
    text: Mapped[str] = mapped_column(Text)
    reply_to_external_id: Mapped[str] = mapped_column(String(200), default="")
    mentions: Mapped[list[str]] = mapped_column(JSON, default=list)
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[OutboundStatus] = mapped_column(Enum(OutboundStatus))
    send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

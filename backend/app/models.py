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


class UserRole(enum.StrEnum):
    admin = "admin"  # 可管理账号、链路、人员
    agent = "agent"  # 技术支持：处理事件、发消息、打标记


class User(Base):
    """后台账号：每个技术支持一个，操作都记在本人名下。"""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))  # 与「我方人员」姓名一致时，可被 @ 到 IM 账号
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.agent)
    active: Mapped[bool] = mapped_column(default=True)
    on_duty: Mapped[bool] = mapped_column(default=False)  # 值班中：参与自动分配
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiSession(Base):
    __tablename__ = "api_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Staff(Base):
    """我方人员在各平台的账号；他们在群里的发言不会被当成客户问题。"""

    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("platform", "account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    account: Mapped[str] = mapped_column(String(200))


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
    # 自由标识，例如 ["VIP", "视频", "海外"]；链路可按标识限定适用的群
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 企业微信群机器人 Webhook 地址（企业微信用它发消息）
    bot_webhook: Mapped[str] = mapped_column(String(500), default="")
    # 与日志库（new-api）的对应关系：客户群 → 用户 ID/用户名；供应商群 → 渠道 ID
    api_users: Mapped[list[str]] = mapped_column(JSON, default=list)
    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workflow(Base):
    """链路：一类问题从识别到处理的规则，由管理员创建。"""

    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 小的先匹配
    # 触发条件：分诊类别（error / rate_limit / usage_request / ...）或关键词，命中任意一项
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 只对带这些标识的群生效；为空表示所有客户群
    group_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 数据查询步骤：request_id / recent_errors / usage / channel
    lookups: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 给 Agent 的处理说明（这条链路怎么判断、怎么回复）
    instructions: Mapped[str] = mapped_column(Text, default="")
    # 判定为供应商问题时是否转交供应商群
    route_supplier: Mapped[bool] = mapped_column(default=True)
    # 覆盖群的自动化策略：""（沿用群设置）/ confirm（一律人工确认）
    automation: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IncidentEvent(Base):
    """事件时间线：每一步处理都记一条，用于跟踪。"""

    __tablename__ = "incident_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("group_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200), default="")
    sender: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text)
    reply_to_external_id: Mapped[str] = mapped_column(String(200), default="")
    from_us: Mapped[bool] = mapped_column(default=False)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", use_alter=True, name="fk_messages_incident_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IncidentStatus(enum.StrEnum):
    open = "open"
    investigating = "investigating"
    waiting_supplier = "waiting_supplier"
    resolved = "resolved"


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"))
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
    # 供应商转交单追踪
    supplier_group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"))
    ticket_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supplier_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supplier_last_reply: Mapped[str] = mapped_column(Text, default="")
    reminders: Mapped[int] = mapped_column(Integer, default=0)
    related_messages: Mapped[int] = mapped_column(Integer, default=1)
    # 数据查询结果快照（给人看，也用于复盘）
    lookup_result: Mapped[str] = mapped_column(Text, default="")
    knowledge_ids: Mapped[list[int]] = mapped_column(JSON, default=list)  # 本次参考的知识
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 已 @ 内部负责人的时间（同一事件只升级一次，重新打开后可再次升级）
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_reason: Mapped[str] = mapped_column(Text, default="")
    # 人工接管到此时间为止：期间 AI 的消息一律等人工确认，不自动发
    human_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_by: Mapped[str] = mapped_column(String(100), default="")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutboundStatus(enum.StrEnum):
    pending = "pending"  # 预发中，等待倒计时或人工
    needs_approval = "needs_approval"  # 分数太低，必须人工
    sending = "sending"  # 已被某个发送方认领，防止重复发送
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
    # AI 原稿（被人工修改过时与 text 不同，用来学习同事的改法）
    original_text: Mapped[str] = mapped_column(Text, default="")
    # 怎么发出去的：auto（倒计时自动）/ approved（人工点发送）/ manual（人工撰写）
    sent_by: Mapped[str] = mapped_column(String(20), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Knowledge(Base):
    """知识沉淀：处理过的问题总结成经验，Agent 排查时自动检索参考。"""

    __tablename__ = "knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # 现象：报错原文、错误码、客户描述等，用于匹配
    symptoms: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    models: Mapped[list[str]] = mapped_column(JSON, default=list)
    responsible: Mapped[str] = mapped_column(String(20), default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    solution: Mapped[str] = mapped_column(Text, default="")
    reply_template: Mapped[str] = mapped_column(Text, default="")
    source_incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    hits: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class GroupMemory(Base):
    """客户记忆：每次事件关闭时沉淀一句话，新会话开始时给 Agent 参考。"""

    __tablename__ = "group_memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

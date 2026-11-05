from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import AutomationMode, GroupKind, IncidentStatus, OutboundStatus, Platform, UserRole


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: Platform
    external_id: str
    name: str
    kind: GroupKind
    party_id: int | None
    models: list[str]
    level: str
    contacts: list[str]
    style: dict
    automation: AutomationMode
    extra_delay_seconds: int
    sla_minutes: int
    owner: str
    backup_owner: str
    tags: list[str]
    api_users: list[str]
    channel_ids: list[int]
    bot_webhook: str


class GroupUpdate(BaseModel):
    name: str | None = None
    kind: GroupKind | None = None
    party_id: int | None = None
    models: list[str] | None = None
    level: str | None = None
    contacts: list[str] | None = None
    style: dict | None = None
    automation: AutomationMode | None = None
    extra_delay_seconds: int | None = None
    sla_minutes: int | None = None
    owner: str | None = None
    backup_owner: str | None = None
    tags: list[str] | None = None
    api_users: list[str] | None = None
    channel_ids: list[int] | None = None
    bot_webhook: str | None = None


class PartyIn(BaseModel):
    name: str
    kind: GroupKind


class PartyOut(PartyIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    sender: str
    text: str
    from_us: bool
    incident_id: int | None
    created_at: datetime


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    category: str
    model: str
    request_id: str
    summary: str
    responsible: str
    evidence: str
    confidence: int
    status: IncidentStatus
    supplier_group_id: int | None
    ticket_sent_at: datetime | None
    supplier_replied_at: datetime | None
    supplier_last_reply: str
    reminders: int
    related_messages: int
    workflow_id: int | None
    lookup_result: str
    knowledge_ids: list[int]
    resolved_at: datetime | None
    escalated_at: datetime | None
    escalation_reason: str
    human_until: datetime | None
    human_by: str
    assignee_id: int | None
    created_at: datetime


class OutboundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    group_name: str = ""
    incident_id: int | None
    purpose: str
    text: str
    mentions: list[str]
    score: int
    status: OutboundStatus
    send_at: datetime | None
    error: str
    created_at: datetime


class OutboundEdit(BaseModel):
    text: str
    editor: str = ""


class SimulateIn(BaseModel):
    """本地调试：模拟一条群消息。"""

    platform: Platform = Platform.telegram
    chat_id: str
    chat_name: str = ""
    sender: str = "tester"
    text: str


class StaffIn(BaseModel):
    name: str
    platform: Platform
    account: str


class StaffOut(StaffIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class IncidentUpdate(BaseModel):
    status: IncidentStatus | None = None
    responsible: str | None = None
    assignee_id: int | None = None
    note: str = ""  # 人工备注，写入时间线
    actor: str = ""


class IncidentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    text: str
    actor: str
    created_at: datetime


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    enabled: bool = True
    priority: int = 100
    categories: list[str] = []
    keywords: list[str] = []
    group_tags: list[str] = []
    lookups: list[Literal["request_id", "recent_errors", "usage", "channel"]] = []
    instructions: str = ""
    route_supplier: bool = True
    automation: Literal["", "confirm"] = ""


class WorkflowOut(WorkflowIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class LookupTest(BaseModel):
    request_id: str = ""
    api_users: list[str] = []
    model: str = ""
    lookups: list[Literal["request_id", "recent_errors", "usage", "channel"]] = [
        "request_id", "recent_errors", "usage", "channel"]


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    symptoms: str = ""
    keywords: list[str] = []
    models: list[str] = []
    responsible: str = ""
    root_cause: str = ""
    solution: str = ""
    reply_template: str = ""
    source_incident_id: int | None = None
    enabled: bool = True


class KnowledgeOut(KnowledgeIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    hits: int
    created_at: datetime
    updated_at: datetime


class StylePreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    style: dict | None = None  # 不传则用群当前的风格（用于编辑中实时试）


class ManualSendIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    incident_id: int | None = None
    reply_to_message_id: int | None = None  # 引用哪条消息（本地消息 ID）
    mentions: list[str] = []
    actor: str = ""


class DraftIn(BaseModel):
    incident_id: int | None = None
    intent: str = ""  # 想说什么；留空则由 AI 根据进展自己判断


class LoginIn(BaseModel):
    username: str
    password: str


class MeUpdate(BaseModel):
    on_duty: bool | None = None
    password: str | None = None
    old_password: str | None = None


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    password: str
    role: UserRole = UserRole.agent
    on_duty: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: UserRole | None = None
    active: bool | None = None
    on_duty: bool | None = None
    password: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: UserRole
    active: bool
    on_duty: bool

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import AutomationMode, GroupKind, IncidentStatus, OutboundStatus, Platform


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
    owner: str
    backup_owner: str


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
    owner: str | None = None
    backup_owner: str | None = None


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

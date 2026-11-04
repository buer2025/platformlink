from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class InboundMessage:
    platform: str
    chat_id: str
    chat_name: str
    message_id: str
    sender: str
    text: str
    reply_to_message_id: str = ""
    mentions: list[str] = field(default_factory=list)
    is_bot: bool = False


class IMAdapter(Protocol):
    platform: str

    def parse_webhook(self, payload: dict) -> InboundMessage | None:
        """把平台回调解析成统一消息；非消息事件返回 None。"""

    async def send(
        self, chat_id: str, text: str, reply_to: str = "", mentions: list[str] | None = None
    ) -> str:
        """发送消息，返回平台消息 ID。"""

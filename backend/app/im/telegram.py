import httpx

from app.config import settings
from app.im.base import InboundMessage


class TelegramAdapter:
    """Telegram Bot API。需在 BotFather 关闭 privacy mode 才能收到群内全部消息。"""

    platform = "telegram"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.telegram_bot_token

    def parse_webhook(self, payload: dict) -> InboundMessage | None:
        msg = payload.get("message") or payload.get("edited_message")
        if not msg or "text" not in msg:
            return None
        chat = msg["chat"]
        sender = msg.get("from", {})
        return InboundMessage(
            platform=self.platform,
            chat_id=str(chat["id"]),
            chat_name=chat.get("title", ""),
            message_id=str(msg["message_id"]),
            sender=sender.get("username") or sender.get("first_name", ""),
            text=msg["text"],
            reply_to_message_id=str(msg.get("reply_to_message", {}).get("message_id", "")),
        )

    async def send(
        self, chat_id: str, text: str, reply_to: str = "", mentions: list[str] | None = None
    ) -> str:
        if mentions:
            text = " ".join(f"@{m.lstrip('@')}" for m in mentions) + " " + text
        body: dict = {"chat_id": chat_id, "text": text}
        if reply_to.isdigit():
            body["reply_parameters"] = {"message_id": int(reply_to)}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json=body)
            r.raise_for_status()
            return str(r.json()["result"]["message_id"])

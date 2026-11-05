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
        if not msg:
            return None
        images = []
        if msg.get("photo"):  # 多种尺寸，取最大的
            images.append(msg["photo"][-1]["file_id"])
        elif str(msg.get("document", {}).get("mime_type", "")).startswith("image/"):
            images.append(msg["document"]["file_id"])
        text = msg.get("text") or msg.get("caption") or ""
        if not text and not images:
            return None
        chat = msg["chat"]
        sender = msg.get("from", {})
        return InboundMessage(
            platform=self.platform,
            chat_id=str(chat["id"]),
            chat_name=chat.get("title", ""),
            message_id=str(msg["message_id"]),
            sender=sender.get("username") or sender.get("first_name", ""),
            text=text or "[图片]",
            reply_to_message_id=str(msg.get("reply_to_message", {}).get("message_id", "")),
            is_bot=bool(sender.get("is_bot")),
            image_ids=images,
        )

    async def fetch_image(self, image_id: str) -> tuple[bytes, str] | None:
        base = f"https://api.telegram.org/bot{self.token}"
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{base}/getFile", params={"file_id": image_id})
            r.raise_for_status()
            path = r.json()["result"]["file_path"]
            if r.json()["result"].get("file_size", 0) > 5 * 1024 * 1024:
                return None
            img = await client.get(f"https://api.telegram.org/file/bot{self.token}/{path}")
            img.raise_for_status()
            ext = path.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            return img.content, mime

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

import json
import time

import httpx

from app.config import settings
from app.im.base import InboundMessage

API = "https://open.feishu.cn/open-apis"


class FeishuAdapter:
    """飞书自建应用机器人：事件订阅 im.message.receive_v1 接收群消息。"""

    platform = "feishu"

    def __init__(self) -> None:
        self._token = ""
        self._token_exp = 0.0

    def parse_webhook(self, payload: dict) -> InboundMessage | None:
        if payload.get("header", {}).get("event_type") != "im.message.receive_v1":
            return None
        event = payload["event"]
        msg = event["message"]
        if msg.get("message_type") != "text":
            return None
        text = json.loads(msg["content"]).get("text", "")
        return InboundMessage(
            platform=self.platform,
            chat_id=msg["chat_id"],
            chat_name="",
            message_id=msg["message_id"],
            sender=event.get("sender", {}).get("sender_id", {}).get("open_id", ""),
            text=text,
            reply_to_message_id=msg.get("parent_id", ""),
            mentions=[m.get("id", {}).get("open_id", "") for m in msg.get("mentions", [])],
        )

    async def _tenant_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        r = await client.post(
            f"{API}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["tenant_access_token"]
        self._token_exp = time.time() + data.get("expire", 7200) - 300
        return self._token

    async def send(
        self, chat_id: str, text: str, reply_to: str = "", mentions: list[str] | None = None
    ) -> str:
        at = "".join(f'<at user_id="{m}"></at> ' for m in mentions or [])
        content = json.dumps({"text": at + text}, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {"Authorization": f"Bearer {await self._tenant_token(client)}"}
            if reply_to:
                r = await client.post(
                    f"{API}/im/v1/messages/{reply_to}/reply",
                    headers=headers,
                    json={"msg_type": "text", "content": content},
                )
            else:
                r = await client.post(
                    f"{API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers=headers,
                    json={"receive_id": chat_id, "msg_type": "text", "content": content},
                )
            r.raise_for_status()
            return r.json()["data"]["message_id"]

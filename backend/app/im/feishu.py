import json
import re
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
        kind = msg.get("message_type")
        content = json.loads(msg.get("content") or "{}")
        images: list[str] = []
        if kind == "text":
            text = re.sub(r"@_user_\d+\s*", "", content.get("text", "")).strip()
        elif kind == "image":
            # 图片要按「消息 ID + image_key」下载
            text, images = "[图片]", [f"{msg['message_id']}:{content.get('image_key', '')}"]
        elif kind == "post":  # 富文本：拼出文字，收集其中的图片
            text_parts = []
            body = content.get("content") or next(
                (v.get("content") for v in content.values() if isinstance(v, dict)), []) or []
            for line in body:
                for el in line:
                    if el.get("tag") == "text":
                        text_parts.append(el.get("text", ""))
                    elif el.get("tag") == "img":
                        images.append(f"{msg['message_id']}:{el.get('image_key', '')}")
            text = (content.get("title", "") + " " + "".join(text_parts)).strip() or "[图片]"
        else:
            return None
        return InboundMessage(
            platform=self.platform,
            chat_id=msg["chat_id"],
            chat_name="",
            message_id=msg["message_id"],
            sender=event.get("sender", {}).get("sender_id", {}).get("open_id", ""),
            text=text,
            reply_to_message_id=msg.get("parent_id", ""),
            mentions=[m.get("id", {}).get("open_id", "") for m in msg.get("mentions", [])],
            is_bot=event.get("sender", {}).get("sender_type") == "app",
            image_ids=images,
        )

    async def fetch_image(self, image_id: str) -> tuple[bytes, str] | None:
        message_id, key = image_id.split(":", 1)
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{API}/im/v1/messages/{message_id}/resources/{key}", params={"type": "image"},
                headers={"Authorization": f"Bearer {await self._tenant_token(client)}"})
            r.raise_for_status()
            if len(r.content) > 5 * 1024 * 1024:
                return None
            return r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]

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


def decrypt_event(encrypted: str, encrypt_key: str) -> dict:
    """飞书事件加密（开启 Encrypt Key 后）：AES-256-CBC，key = sha256(Encrypt Key)，前 16 字节为 IV。"""
    import base64
    import hashlib

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    raw = base64.b64decode(encrypted)
    key = hashlib.sha256(encrypt_key.encode()).digest()
    dec = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded = dec.update(raw[16:]) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return json.loads(unpad.update(padded) + unpad.finalize())


def verify_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes, signature: str) -> bool:
    """飞书请求签名：sha256(timestamp + nonce + encrypt_key + body)。"""
    import hashlib
    import hmac
    expect = hashlib.sha256((timestamp + nonce + encrypt_key).encode() + body).hexdigest()
    return hmac.compare_digest(expect, signature)

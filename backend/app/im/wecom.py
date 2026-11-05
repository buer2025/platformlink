from app.im.base import InboundMessage


class WeComAdapter:
    """企业微信。

    接收：外部群消息需通过「会话内容存档」SDK 拉取（需开通权限，C SDK + RSA 解密），
    拉取进程把消息转换成本接口的 payload 格式后 POST 到 /api/webhooks/wecom。
    发送：企业微信「群机器人」Webhook（在群设置里添加机器人，复制 Webhook 地址填到后台群设置）。
    限制：群机器人只能加到内部群；外部客户群企业微信不允许机器人主动发消息，
    这类群请把自动化设为「需人工确认」，由同事复制 AI 草稿发送。
    """

    platform = "wecom"

    def parse_webhook(self, payload: dict) -> InboundMessage | None:
        if payload.get("msgtype") != "text":
            return None
        return InboundMessage(
            platform=self.platform,
            chat_id=payload["roomid"],
            chat_name=payload.get("roomname", ""),
            message_id=payload["msgid"],
            sender=payload.get("from", ""),
            text=payload["text"]["content"],
        )

    async def send(
        self, chat_id: str, text: str, reply_to: str = "", mentions: list[str] | None = None,
        webhook: str = "",
    ) -> str:
        if not webhook:
            raise RuntimeError("这个企业微信群没有配置群机器人 Webhook（群设置 → 添加机器人），"
                               "外部客户群请人工发送")
        import uuid

        import httpx
        body = {"msgtype": "text", "text": {"content": text, "mentioned_list": list(mentions or [])}}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(webhook, json=body)
            r.raise_for_status()
            data = r.json()
            if data.get("errcode"):
                raise RuntimeError(f"企业微信返回错误 {data.get('errcode')}: {data.get('errmsg')}")
        return f"wecom-bot-{uuid.uuid4().hex[:12]}"  # 群机器人接口不返回消息 ID

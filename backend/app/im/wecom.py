from app.im.base import InboundMessage


class WeComAdapter:
    """企业微信。

    接收：外部群消息需通过「会话内容存档」SDK 拉取（需开通权限，C SDK + RSA 解密），
    拉取进程把消息转换成本接口的 payload 格式后 POST 到 /api/webhooks/wecom。
    发送：自建应用 / 客户群接口。TODO：开通权限后实现。
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
        self, chat_id: str, text: str, reply_to: str = "", mentions: list[str] | None = None
    ) -> str:
        raise NotImplementedError("企业微信发送待开通权限后实现")

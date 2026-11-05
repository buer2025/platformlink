import json

from app.agent.llm import _rule_classify
from app.im.feishu import FeishuAdapter
from app.im.telegram import TelegramAdapter
from app.im.wecom import WeComAdapter


def test_telegram_parse():
    m = TelegramAdapter("x").parse_webhook({"message": {
        "message_id": 5, "chat": {"id": -100, "title": "G"}, "from": {"username": "u"},
        "text": "hi", "reply_to_message": {"message_id": 3}}})
    assert (m.chat_id, m.message_id, m.reply_to_message_id, m.sender) == ("-100", "5", "3", "u")
    assert TelegramAdapter("x").parse_webhook({"message": {"message_id": 1, "chat": {"id": 1}}}) is None


def test_feishu_parse_strips_mentions_and_flags_bot():
    payload = {"header": {"event_type": "im.message.receive_v1"}, "event": {
        "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
        "message": {"message_id": "om_1", "chat_id": "oc_1", "message_type": "text",
                    "parent_id": "om_0",
                    "content": json.dumps({"text": "@_user_1 seedance 报错了"})}}}
    m = FeishuAdapter().parse_webhook(payload)
    assert m.text == "seedance 报错了" and m.reply_to_message_id == "om_0" and not m.is_bot
    payload["event"]["sender"]["sender_type"] = "app"
    assert FeishuAdapter().parse_webhook(payload).is_bot


def test_wecom_parse():
    m = WeComAdapter().parse_webhook({"msgtype": "text", "roomid": "r1", "msgid": "m1",
                                      "from": "u", "text": {"content": "hi"}})
    assert (m.chat_id, m.text) == ("r1", "hi")


def test_rule_classify():
    c = _rule_classify("Seedance-1.0 一直 429 request_id: req-abc12345")
    assert c.is_issue and c.category == "rate_limit" and c.request_id == "req-abc12345"
    assert c.model == "seedance-1.0"
    assert not _rule_classify("谢谢大家").is_issue


def _feishu_encrypt(obj: dict, key: str) -> str:
    import base64
    import hashlib
    import json
    import os

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    data = p.update(json.dumps(obj).encode()) + p.finalize()
    enc = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + enc.update(data) + enc.finalize()).decode()


def test_feishu_decrypt_and_signature():
    from app.im.feishu import decrypt_event, verify_signature
    enc = _feishu_encrypt({"type": "url_verification", "challenge": "abc"}, "k3y")
    assert decrypt_event(enc, "k3y")["challenge"] == "abc"
    import hashlib
    body = b'{"encrypt":"x"}'
    sig = hashlib.sha256(b"1" + b"n" + b"k3y" + body).hexdigest()
    assert verify_signature("1", "n", "k3y", body, sig)
    assert not verify_signature("1", "n", "k3y", body, "bad")


def test_feishu_image_and_post_parse():
    import json

    from app.im.feishu import FeishuAdapter
    base = {"header": {"event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_id": {"open_id": "ou_1"}}, "message": {
                "chat_id": "oc_1", "message_id": "om_1"}}}
    base["event"]["message"].update(message_type="image", content=json.dumps({"image_key": "img_1"}))
    m = FeishuAdapter().parse_webhook(base)
    assert m.image_ids == ["om_1:img_1"] and m.text == "[图片]"
    base["event"]["message"].update(message_type="post", content=json.dumps({"title": "报错", "content": [
        [{"tag": "text", "text": "seedance 429 了"}], [{"tag": "img", "image_key": "img_2"}]]}))
    m = FeishuAdapter().parse_webhook(base)
    assert m.text == "报错 seedance 429 了" and m.image_ids == ["om_1:img_2"]


async def test_wecom_webhook_send(monkeypatch):
    import httpx

    from app.im.wecom import WeComAdapter
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"errcode": 0}

    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json):
            sent.update(url=url, body=json)
            return Resp()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    ext = await WeComAdapter().send("room", "你好", mentions=["zhangsan"], webhook="https://qyapi/x?key=1")
    assert ext.startswith("wecom-bot-") and sent["body"]["text"]["mentioned_list"] == ["zhangsan"]
    import pytest
    with pytest.raises(RuntimeError, match="Webhook"):
        await WeComAdapter().send("room", "你好")

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

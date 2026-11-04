from app.im.base import IMAdapter
from app.im.feishu import FeishuAdapter
from app.im.telegram import TelegramAdapter
from app.im.wecom import WeComAdapter

_adapters: dict[str, IMAdapter] = {
    "telegram": TelegramAdapter(),
    "feishu": FeishuAdapter(),
    "wecom": WeComAdapter(),
}


def get_adapter(platform: str) -> IMAdapter:
    return _adapters[platform]


def set_adapter(platform: str, adapter: IMAdapter) -> None:
    """测试时替换为假适配器。"""
    _adapters[platform] = adapter

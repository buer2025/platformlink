from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./platformlink.db"
    anthropic_model: str = "claude-opus-5-5"
    # 为空时 Agent 使用规则兜底（便于本地开发和测试）
    anthropic_api_key: str = ""

    # 后台登录口令（必须配置；为空时拒绝所有后台请求）
    admin_token: str = ""
    # Webhook 校验
    telegram_webhook_secret: str = ""  # setWebhook 的 secret_token
    feishu_verification_token: str = ""
    wecom_forward_secret: str = ""  # 存档拉取进程转发时带的 X-Forward-Secret

    telegram_bot_token: str = ""
    # webhook：Telegram 推送到 /api/webhooks/telegram（需公网 HTTPS）
    # polling：程序主动拉取消息（无公网地址时用，例如本地或内网部署）
    telegram_mode: str = "webhook"
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    wecom_corp_id: str = ""
    wecom_agent_secret: str = ""

    # 预发分档：(最低分, 倒计时秒数)；低于最后一档必须人工确认
    presend_tiers: list[tuple[int, int]] = [(90, 120), (70, 600)]
    scheduler_interval_seconds: float = 5.0
    # 同群同类问题在此时间内合并为一个事件
    incident_merge_minutes: int = 30
    # 供应商转交单无回复多久后催办，最多催几次
    supplier_remind_minutes: int = 30
    supplier_max_reminders: int = 2


settings = Settings()

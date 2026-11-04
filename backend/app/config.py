from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./platformlink.db"
    anthropic_model: str = "claude-opus-5-5"
    # 为空时 Agent 使用规则兜底（便于本地开发和测试）
    anthropic_api_key: str = ""

    telegram_bot_token: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    wecom_corp_id: str = ""
    wecom_agent_secret: str = ""

    # 预发分档：(最低分, 倒计时秒数)；低于最后一档必须人工确认
    presend_tiers: list[tuple[int, int]] = [(90, 120), (70, 600)]
    scheduler_interval_seconds: float = 5.0


settings = Settings()

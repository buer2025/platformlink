from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./platformlink.db"
    anthropic_model: str = "claude-opus-5-5"
    # 为空时 Agent 使用规则兜底（便于本地开发和测试）
    anthropic_api_key: str = ""
    # 中转 / 代理地址；为空时使用官方 API
    anthropic_base_url: str = ""

    # 后台登录口令（必须配置；为空时拒绝所有后台请求）
    admin_token: str = ""
    # 日志库（new-api 结构，只读账号），例如 mysql+aiomysql://ro:pwd@host:3306/new-api
    # 为空时链路中的数据查询步骤会跳过
    log_database_url: str = ""
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
    # 未解决事件在多久内承接客户的追问 / 催促（持续对话）
    conversation_hours: int = 6
    # 未解决事件多久没有动静自动关闭（并沉淀客户记忆）
    incident_idle_hours: int = 24
    # 升级人工：Agent 准确率低于此值，或客户追问达到次数时 @ 内部负责人
    escalate_below_confidence: int = 50
    escalate_after_followups: int = 2
    # 供应商转交单无回复多久后催办，最多催几次
    supplier_remind_minutes: int = 30
    supplier_max_reminders: int = 2


settings = Settings()

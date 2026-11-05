import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(), "test.db")
_url = os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_db}")
os.environ.update(
    DATABASE_URL=_url,
    ANTHROPIC_API_KEY="",
    ANTHROPIC_BASE_URL="",
    LOG_DATABASE_URL="",
    JEV_MODEL="",
    TELEGRAM_MODE="webhook",
    ADMIN_TOKEN="test-admin",
    TELEGRAM_WEBHOOK_SECRET="tg-secret",
    FEISHU_VERIFICATION_TOKEN="fs-token",
    WECOM_FORWARD_SECRET="wc-secret",
)

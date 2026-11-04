import hmac

from fastapi import Header, HTTPException, Request

from app.config import settings


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def require_admin(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not _eq(token, settings.admin_token):
        raise HTTPException(401, "未登录或口令错误")


def verify_webhook(platform: str, request: Request, payload: dict) -> bool:
    h = request.headers
    if platform == "telegram":
        return _eq(h.get("x-telegram-bot-api-secret-token", ""), settings.telegram_webhook_secret)
    if platform == "feishu":
        token = payload.get("token") or payload.get("header", {}).get("token", "")
        return _eq(token, settings.feishu_verification_token)
    if platform == "wecom":
        return _eq(h.get("x-forward-secret", ""), settings.wecom_forward_secret)
    return False

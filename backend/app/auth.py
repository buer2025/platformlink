import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ApiSession, User, UserRole

SESSION_DAYS = 14
DbSession = Depends(get_session)


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
    except ValueError:
        return False
    got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got.hex(), dk)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Actor:
    """当前操作者。用 ADMIN_TOKEN 登录时是内置超级管理员（id=None）。"""

    id: int | None
    name: str
    role: UserRole

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


async def create_session(s: AsyncSession, user: User) -> str:
    from app.services.presend import utcnow
    token = secrets.token_urlsafe(32)
    s.add(ApiSession(token_hash=_token_hash(token), user_id=user.id,
                     expires_at=utcnow() + timedelta(days=SESSION_DAYS)))
    await s.commit()
    return token


async def current_actor(authorization: str = Header(default=""),
                        s: AsyncSession = DbSession) -> Actor:
    from app.services.presend import aware, utcnow
    token = authorization.removeprefix("Bearer ").strip()
    from app.services import audit
    if _eq(token, settings.admin_token):
        audit.set_actor(None, "管理员（ADMIN_TOKEN）", UserRole.admin)
        return Actor(None, "管理员", UserRole.admin)
    if token:
        sess = await s.scalar(select(ApiSession).where(ApiSession.token_hash == _token_hash(token)))
        if sess and aware(sess.expires_at) > utcnow():
            user = await s.get(User, sess.user_id)
            if user and user.active:
                audit.set_actor(user.id, user.display_name, user.role)
                return Actor(user.id, user.display_name, user.role)
    raise HTTPException(401, "未登录或登录已过期")


# 兼容旧名字：所有后台接口都要求登录
require_admin = current_actor


CurrentActor = Depends(current_actor)


async def admin_only(actor: Actor = CurrentActor) -> Actor:
    if not actor.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return actor


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

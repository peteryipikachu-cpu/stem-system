from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import User

SESSION_COOKIE = "stem_session"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    # PBKDF2-HMAC 是 Python 标准库在所有部署镜像中均可用的密码派生函数。
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 600_000)
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _urlsafe(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(user: User) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.auth_session_hours)
    payload = {"uid": user.id, "exp": int(expires_at.timestamp())}
    encoded = _urlsafe(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(settings.auth_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_urlsafe(signature)}"


def parse_session_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = hmac.new(get_settings().auth_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(supplied_signature)):
            return None
        payload: dict[str, Any] = json.loads(_decode(encoded))
        if int(payload["exp"]) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return int(payload["uid"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def user_view(user: User) -> dict[str, Any]:
    expires_at = user.expires_at
    is_expired = expires_at is not None and expires_at <= datetime.now(timezone.utc)
    return {"id": user.id, "username": user.username, "role": user.role, "isActive": bool(user.is_active) and not is_expired,
            "isExpired": is_expired,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "createdAt": user.created_at.isoformat() if user.created_at else None}


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    user_id = parse_session_token(request.cookies.get(SESSION_COOKIE))
    user = await session.get(User, user_id) if user_id else None
    if not user or not user.is_active or (user.expires_at is not None and user.expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


async def ensure_initial_admin(session: AsyncSession) -> None:
    existing_user = await session.scalar(select(User.id).limit(1))
    if existing_user is not None:
        return
    settings = get_settings()
    session.add(User(username=settings.initial_admin_username, password_hash=hash_password(settings.initial_admin_password), role="admin"))
    await session.commit()

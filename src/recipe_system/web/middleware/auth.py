"""Firebase Authentication ID トークン検証.

Cloud Run では `--allow-unauthenticated` で受けて、ここでアプリ側検証する方針.
エミュレータ接続時は `FIREBASE_AUTH_EMULATOR_HOST` を Admin SDK が自動で拾う.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from recipe_system.config import get_settings
from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)

SESSION_KEY = "auth"


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str
    email_verified: bool
    family_id: str | None = None


def init_firebase() -> None:
    """Admin SDK を遅延初期化する. 既に初期化済みなら何もしない."""
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:  # pragma: no cover - private API but documented
        return
    settings = get_settings()
    try:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred, {"projectId": settings.google_cloud_project})
    except Exception:
        firebase_admin.initialize_app(options={"projectId": settings.google_cloud_project})


async def verify_id_token(id_token: str) -> dict[str, Any]:
    from firebase_admin import auth as firebase_auth

    init_firebase()
    result: dict[str, Any] = firebase_auth.verify_id_token(id_token, check_revoked=False)
    return result


def current_user(request: Request) -> AuthenticatedUser:
    """保護エンドポイントで使う. セッション未確立なら 401."""
    data = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
    if not data or not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未認証")
    try:
        return AuthenticatedUser(
            uid=data["uid"],
            email=data["email"],
            email_verified=bool(data.get("email_verified", False)),
            family_id=data.get("family_id"),
        )
    except KeyError as e:
        logger.warning("auth.session_malformed", missing=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="セッションが壊れています"
        ) from e


def require_family(user: AuthenticatedUser) -> str:
    if not user.family_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="家族メンバーとして登録されていません。管理者にご連絡ください。",
        )
    return user.family_id

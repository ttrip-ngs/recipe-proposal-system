"""認証系ルート.

/login: Firebase Auth SDK を読み込むログイン画面 (クライアント側で ID トークン取得)
/session: POST で ID トークンを受けてサーバセッションを確立
/logout: POST でセッション破棄
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from recipe_system.observability.logging import get_logger
from recipe_system.repository.family_repository import find_family_id_by_email
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.web.middleware.auth import SESSION_KEY, verify_id_token
from recipe_system.web.templating import templates

router = APIRouter()
logger = get_logger(__name__)


class SessionRequest(BaseModel):
    id_token: str


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    from recipe_system.config import get_settings

    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "firebase_config": settings.firebase_web_config,
            "firebase_auth_emulator_host_browser": (
                settings.firebase_auth_emulator_host_browser if settings.is_emulator else None
            ),
            "dev_login_email": settings.dev_login_email if settings.is_emulator else None,
            "dev_login_password": (settings.dev_login_password if settings.is_emulator else None),
        },
    )


@router.post("/session")
async def create_session(request: Request, payload: SessionRequest) -> JSONResponse:
    try:
        decoded = await verify_id_token(payload.id_token)
    except Exception as e:
        logger.warning("auth.verify_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="無効な ID トークン"
        ) from e

    email = decoded.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="メールアドレス未取得")

    client = get_firestore_client()
    family_id = find_family_id_by_email(client, email)
    if family_id is None:
        logger.warning("auth.email_not_whitelisted", email=email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="このアカウントは家族メンバーとして登録されていません。",
        )

    request.session[SESSION_KEY] = {
        "uid": decoded["uid"],
        "email": email,
        "email_verified": bool(decoded.get("email_verified", False)),
        "family_id": family_id,
    }
    logger.info("auth.session_established", email=email, family_id=family_id)
    return JSONResponse({"status": "ok", "redirect": "/"})


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

"""FastAPI エントリポイント."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from recipe_system import __version__
from recipe_system.config import get_settings
from recipe_system.guardrails.dictionary_loader import default_dictionary
from recipe_system.observability.logging import configure_logging, get_logger
from recipe_system.web.routes import (
    admin,
    auth,
    dashboard,
    health,
    history,
    plans,
    profile,
    profile_edit,
    proposals,
    shopping,
)
from recipe_system.web.routes import (
    calendar as calendar_routes,
)
from recipe_system.web.templating import STATIC_DIR, templates


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info(
        "startup",
        version=__version__,
        emulator=settings.is_emulator,
        llm_provider=settings.effective_llm_provider,
        use_prompt_cache=settings.use_prompt_cache,
    )
    # アレルゲン辞書の不整合 (重複登録など) はロード時に例外になる. 遅延ロードのままだと
    # リビジョンは healthy になり最初の提案リクエストで 500 になるため, 起動時に読んで止める.
    default_dictionary()
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Recipe Proposal System",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="recipe_session",
        same_site="lax",
        https_only=not settings.is_emulator,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(calendar_routes.router)
    app.include_router(plans.router)
    app.include_router(proposals.router)
    app.include_router(shopping.router)
    app.include_router(profile.router)
    app.include_router(profile_edit.router)
    app.include_router(history.router)
    app.include_router(admin.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> HTMLResponse | RedirectResponse | JSONResponse:
        if _prefers_json(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED and request.method == "GET":
            return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "errors/error.html",
            {"detail": exc.detail, "status_code": exc.status_code}
            | _error_page_context(exc.status_code),
            status_code=exc.status_code,
        )

    return app


def _error_page_context(status_code: int) -> dict[str, object]:
    """errors/error.html に渡す表示用テキストをステータスコード別に組み立てる.

    旧 errors/{403,404,500}.html 3 枚を 1 テンプレートへ統合したための分岐.
    """
    if status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        return {
            "error_kicker": "403",
            "error_title": "アクセスできません",
            "error_message": "このページを閲覧する権限がありません。",
            "show_login_link": True,
        }
    if status_code == status.HTTP_404_NOT_FOUND:
        return {
            "error_kicker": "404",
            "error_title": "見つかりませんでした",
            "error_message": "お探しのページは見つかりませんでした。",
            "show_login_link": False,
        }
    return {
        "error_kicker": f"Error {status_code}",
        "error_title": "エラーが発生しました",
        "error_message": "しばらく時間を置いて再度お試しください。",
        "show_login_link": False,
    }


def _prefers_json(request: Request) -> bool:
    """XHR/fetch リクエストや /session など JSON 応答が適切なクライアントを判定."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return True
    content_type = request.headers.get("content-type", "")
    return "application/json" in content_type


app = create_app()

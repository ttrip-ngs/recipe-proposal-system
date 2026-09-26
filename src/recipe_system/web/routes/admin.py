"""管理者向け画面 (LLM 利用状況・コスト).

家庭利用前提のため role 概念は持たず、認証済み + 家族メンバー登録済みなら
全員がアクセス可能とする (CLAUDE.md 6 章の YAGNI 原則).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from recipe_system.config import get_settings
from recipe_system.domain.llm_usage import year_month_of
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.llm_budget_repository import (
    derive_budget_status,
    get_budget_jpy,
    get_warn_threshold_pct,
)
from recipe_system.repository.llm_usage_repository import monthly_summary
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter(prefix="/admin")


@router.get("/usage", response_class=HTMLResponse)
async def usage_overview(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    """今月の LLM 利用状況と直近 30 日の日別コストを表示する."""
    family_id = require_family(user)
    client = get_firestore_client()

    now = datetime.now(UTC)
    ym = year_month_of(now)

    summary = monthly_summary(client, family_id=family_id, year_month=ym)
    budget = get_budget_jpy(client)
    warn_pct = get_warn_threshold_pct(client)
    settings = get_settings()

    usage_pct, tone = derive_budget_status(
        current_jpy=summary.total_jpy,
        budget_jpy=budget,
        warn_pct=warn_pct,
    )

    by_day_max = max((v for _, v in summary.by_day), default=0.0)

    return templates.TemplateResponse(
        request,
        "admin/usage.html",
        {
            "user": user,
            "year_month": ym,
            "summary": summary,
            "budget_jpy": budget,
            "warn_pct": warn_pct,
            "usage_pct": usage_pct,
            "tone": tone,
            "by_day_max": by_day_max,
            "usd_jpy_rate": settings.usd_jpy_rate,
            "llm_provider": settings.effective_llm_provider,
            "active_tab": "admin",
        },
    )

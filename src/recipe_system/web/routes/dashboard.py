"""TOP ページ (ダッシュボード).

今週ストリップと今日のカードを表示する. 月ビューへの導線はタブから提供.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from google.cloud import firestore

from recipe_system.domain.llm_usage import year_month_of
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.llm_budget_repository import derive_budget_status, get_budget_config
from recipe_system.repository.llm_usage_repository import monthly_total_jpy
from recipe_system.repository.meal_plan_repository import list_meal_plans_in_range
from recipe_system.repository.shopping_list_repository import get_shopping_list
from recipe_system.services.calendar_view import (
    build_week_view,
    today_in_jst,
    week_range,
)
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    today = today_in_jst()
    week_start, week_end = week_range(today)

    client = get_firestore_client()
    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=week_start,
        end_date=week_end,
    )
    week_view = build_week_view(plans, week_start=week_start, today=today)
    today_cell = next((c for c in week_view.days if c.plan_date == today), None)
    shopping_list = get_shopping_list(client, family_id, week_start)

    budget_widget = _build_budget_widget(client, family_id=family_id)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "today": today,
            "today_cell": today_cell,
            "week": week_view,
            "shopping_list": shopping_list,
            "budget_widget": budget_widget,
            "active_tab": "week",
        },
    )


def _build_budget_widget(client: firestore.Client, *, family_id: str) -> dict[str, object] | None:
    """ウィジェット用 context を組み立てる. Firestore 取得失敗時は None を返し表示省略."""
    try:
        ym = year_month_of(today_in_jst())
        budget_config = get_budget_config(client)
        current = monthly_total_jpy(client, family_id=family_id, year_month=ym)
    except Exception:
        return None
    usage_pct, tone = derive_budget_status(
        current_jpy=current,
        budget_jpy=budget_config.monthly_jpy_limit,
        warn_pct=budget_config.warn_threshold_pct,
    )
    return {
        "current_jpy": current,
        "budget_jpy": budget_config.monthly_jpy_limit,
        "usage_pct": usage_pct,
        "tone": tone,
        "warn_pct": budget_config.warn_threshold_pct,
    }

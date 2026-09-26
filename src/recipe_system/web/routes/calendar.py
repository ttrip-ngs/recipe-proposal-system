"""カレンダー画面 (週・月).

URL クエリで指定の週・月を表示する. パラメータ未指定時は今週/今月.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.meal_plan_repository import list_meal_plans_in_range
from recipe_system.services.calendar_view import (
    build_month_view,
    build_week_view,
    month_range_dates,
    today_in_jst,
    week_range,
)
from recipe_system.web.dates import parse_date_or_default
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


@router.get("/calendar/week", response_class=HTMLResponse)
async def week_view(
    request: Request,
    start: str | None = Query(default=None, description="週の任意の日 (月曜に丸める)"),
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    today = today_in_jst()
    anchor = parse_date_or_default(start, today)
    week_start, week_end = week_range(anchor)

    client = get_firestore_client()
    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=week_start,
        end_date=week_end,
    )
    week = build_week_view(plans, week_start=week_start, today=today)

    current_monday = week_range(today)[0]
    return templates.TemplateResponse(
        request,
        "calendar/week.html",
        {
            "user": user,
            "today": today,
            "week": week,
            "active_tab": "week",
            "can_propose_week": week.week_start >= current_monday,
        },
    )


@router.get("/calendar/month", response_class=HTMLResponse)
async def month_view(
    request: Request,
    year: int | None = Query(default=None, ge=1970, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    today = today_in_jst()
    target_year = year or today.year
    target_month = month or today.month

    grid_start, grid_end = month_range_dates(target_year, target_month)
    client = get_firestore_client()
    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=grid_start,
        end_date=grid_end,
    )
    month_view_data = build_month_view(
        plans,
        year=target_year,
        month=target_month,
        today=today,
    )

    prev_year, prev_month = _shift_month(target_year, target_month, -1)
    next_year, next_month = _shift_month(target_year, target_month, +1)

    return templates.TemplateResponse(
        request,
        "calendar/month.html",
        {
            "user": user,
            "today": today,
            "month_view": month_view_data,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "active_tab": "month",
        },
    )


_MONTHS_IN_YEAR = 12


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    new_month = month + delta
    if new_month < 1:
        return year - 1, _MONTHS_IN_YEAR
    if new_month > _MONTHS_IN_YEAR:
        return year + 1, 1
    return year, new_month

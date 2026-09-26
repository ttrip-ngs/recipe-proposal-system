"""週次買い物リスト画面."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from recipe_system.domain import ShoppingAmount, ShoppingItem
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.meal_plan_repository import list_meal_plans_in_range
from recipe_system.repository.shopping_list_repository import (
    add_item,
    get_shopping_list,
    remove_item,
    toggle_item,
    upsert_shopping_list,
)
from recipe_system.services.calendar_view import today_in_jst, week_range
from recipe_system.services.shopping_aggregator import aggregate_week
from recipe_system.web.dates import parse_date_or_default, require_monday
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


@router.get("/shopping/week", response_class=HTMLResponse)
async def show_week(
    request: Request,
    start: str | None = Query(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    today = today_in_jst()
    anchor = parse_date_or_default(start, today)
    week_start, week_end = week_range(anchor)

    client = get_firestore_client()
    shopping_list = get_shopping_list(client, family_id, week_start)

    return templates.TemplateResponse(
        request,
        "shopping/week.html",
        {
            "user": user,
            "today": today,
            "week_start": week_start,
            "week_end": week_end,
            "prev_week_start": week_start - timedelta(days=7),
            "next_week_start": week_start + timedelta(days=7),
            "shopping_list": shopping_list,
        },
    )


@router.post("/shopping/week/{week_start}/regenerate")
async def regenerate_week(
    week_start: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    ws = require_monday(parse_date_or_default(week_start, today_in_jst()))
    end = ws + timedelta(days=6)
    client = get_firestore_client()

    existing = get_shopping_list(client, family_id, ws)
    preserve = existing.items if existing else ()

    plans = list_meal_plans_in_range(client, family_id, start_date=ws, end_date=end)
    new_list = aggregate_week(
        plans,
        family_id=family_id,
        week_start=ws,
        week_end=end,
        preserve_items=preserve,
    )
    upsert_shopping_list(client, new_list)
    return RedirectResponse(
        url=f"/shopping/week?start={ws.isoformat()}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/shopping/week/{week_start}/items/{item_id}/toggle")
async def toggle_week_item(
    week_start: str,
    item_id: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    ws = require_monday(parse_date_or_default(week_start, today_in_jst()))
    client = get_firestore_client()
    toggled = toggle_item(client, family_id, ws, item_id)
    if toggled is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="買い物リストが見つかりません。",
        )
    return RedirectResponse(
        url=f"/shopping/week?start={ws.isoformat()}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/shopping/week/{week_start}/items/{item_id}/delete")
async def delete_week_item(
    week_start: str,
    item_id: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    ws = require_monday(parse_date_or_default(week_start, today_in_jst()))
    client = get_firestore_client()
    remove_item(client, family_id, ws, item_id)
    return RedirectResponse(
        url=f"/shopping/week?start={ws.isoformat()}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/shopping/week/{week_start}/items")
async def add_week_item(
    week_start: str,
    canonical: str = Form(...),
    quantity: str | None = Form(default=None),
    unit: str | None = Form(default=None),
    note: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    ws = require_monday(parse_date_or_default(week_start, today_in_jst()))
    if not canonical.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="食材名は必須です。")
    try:
        qty = float(quantity) if quantity and quantity.strip() else None
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="数量は数値で指定してください。"
        ) from e

    client = get_firestore_client()
    add_item(
        client,
        family_id,
        ws,
        ShoppingItem(
            canonical=canonical.strip(),
            amounts=(
                (ShoppingAmount(quantity=qty, unit=(unit.strip() or None) if unit else None),)
                if qty is not None
                else ()
            ),
            note=(note.strip() or None) if note else None,
            manual=True,
        ),
    )
    return RedirectResponse(
        url=f"/shopping/week?start={ws.isoformat()}",
        status_code=status.HTTP_303_SEE_OTHER,
    )

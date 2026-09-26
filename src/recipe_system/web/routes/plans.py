"""日別・週間 献立予定の操作.

GET  /plans/{date}
GET  /plans/{date}/status
POST /plans/{date}/propose
POST /plans/{date}/confirm
POST /plans/{date}/cooked
POST /plans/{date}/skip
POST /plans/{date}/clear
POST /plans/{date}/feedback
GET  /plans/week/{week_start}/review
GET  /plans/week/{week_start}/status
POST /plans/week/{week_start}/propose
POST /plans/week/{week_start}/confirm-all
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from recipe_system.cost import CostEstimate, estimate_meal_plan, load_price_book
from recipe_system.domain import MealPlan, can_overwrite
from recipe_system.observability.logging import get_logger
from recipe_system.repository.feedback_repository import save_feedback
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.meal_plan_repository import (
    delete_meal_plan,
    get_meal_plan,
    list_meal_plans_in_range,
    update_status,
    upsert_meal_plan,
)
from recipe_system.repository.proposal_repository import (
    get_proposal,
    pending_placeholder_record,
    save_proposal_record,
)
from recipe_system.services.calendar_view import build_week_view, monday_of, today_in_jst
from recipe_system.services.proposal_workflow import run_single_suggestion, run_weekly_suggestion
from recipe_system.web.dates import parse_date, require_monday
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()
logger = get_logger(__name__)


@router.get("/plans/{plan_date}", response_class=HTMLResponse)
async def show_day(
    plan_date: str,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()

    meal_plan = get_meal_plan(client, family_id, d)
    proposal = None
    if meal_plan and meal_plan.proposal_id:
        proposal = get_proposal(client, meal_plan.proposal_id)

    today = today_in_jst()
    return templates.TemplateResponse(
        request,
        "plans/day.html",
        {
            "user": user,
            "plan_date": d,
            "meal_plan": meal_plan,
            "proposal": proposal,
            "today": today,
            "prev_date": (d - timedelta(days=1)).isoformat(),
            "next_date": (d + timedelta(days=1)).isoformat(),
            "is_today": d == today,
            "is_past": d < today,
        },
    )


@router.get("/plans/{plan_date}/status")
async def day_status(
    plan_date: str,
    user: AuthenticatedUser = Depends(current_user),
) -> dict[str, bool]:
    """生成完了ポーリング用. proposal が pending なら done: false を返す."""
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()

    meal_plan = get_meal_plan(client, family_id, d)
    proposal = None
    if meal_plan and meal_plan.proposal_id:
        proposal = get_proposal(client, meal_plan.proposal_id)

    is_pending = bool(proposal and proposal.get("status") == "pending")
    return {"done": not is_pending}


@router.post("/plans/{plan_date}/propose")
async def propose_for_day(
    plan_date: str,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    today = today_in_jst()
    if d < today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="過去日への提案リクエストはできません。",
        )

    client = get_firestore_client()

    proposal_id = save_proposal_record(
        client,
        family_id=family_id,
        record=pending_placeholder_record() | {"plan_date": d.isoformat()},
        requested_at=datetime.now(UTC),
    )
    upsert_meal_plan(
        client,
        MealPlan(
            family_id=family_id,
            plan_date=d,
            status="proposed",
            proposal_id=proposal_id,
            dishes=(),
            source="single",
        ),
    )

    background_tasks.add_task(run_single_suggestion, family_id, d, proposal_id)

    return RedirectResponse(url=f"/plans/{d.isoformat()}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/plans/{plan_date}/confirm")
async def confirm_day(
    plan_date: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()
    existing = get_meal_plan(client, family_id, d)
    if existing is None or not existing.dishes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="確定する献立がありません。",
        )
    update_status(client, family_id, d, status="confirmed")
    return RedirectResponse(url=f"/plans/{d.isoformat()}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/plans/{plan_date}/cooked")
async def mark_cooked(
    plan_date: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()
    existing = get_meal_plan(client, family_id, d)
    if existing is None or not existing.dishes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="調理済として記録する献立がありません。",
        )
    update_status(client, family_id, d, status="cooked")
    return RedirectResponse(url=f"/plans/{d.isoformat()}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/plans/{plan_date}/skip")
async def skip_day(
    plan_date: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()
    update_status(client, family_id, d, status="skipped")
    return RedirectResponse(url=f"/plans/{d.isoformat()}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/plans/{plan_date}/clear")
async def clear_day(
    plan_date: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    client = get_firestore_client()
    delete_meal_plan(client, family_id, d)
    return RedirectResponse(url=f"/plans/{d.isoformat()}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/plans/{plan_date}/feedback")
async def submit_feedback(
    plan_date: str,
    request: Request,
    rating: str = Form(...),
    comment: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    d = parse_date(plan_date)
    if rating not in ("good", "neutral", "pass"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="無効な rating")
    client = get_firestore_client()
    meal_plan = get_meal_plan(client, family_id, d)
    if meal_plan is None or not meal_plan.proposal_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="フィードバック対象の提案がありません。",
        )
    save_feedback(
        client,
        family_id=family_id,
        proposal_id=meal_plan.proposal_id,
        rating=rating,  # type: ignore[arg-type]
        comment=comment,
    )
    return templates.TemplateResponse(
        request,
        "shared/feedback_sent.html",
        {
            "user": user,
            "nav_section": "today",
            "heading": "フィードバックを受け取りました",
            "message": "次の提案に反映されます。",
            "back_url": f"/plans/{d.isoformat()}",
            "back_label": "この日に戻る",
            "secondary_url": "/",
            "secondary_label": "ダッシュボードへ",
        },
    )


@router.post("/plans/week/{week_start}/propose")
async def propose_for_week(
    week_start: str,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    """週分まとめて提案. week_start は月曜のみ受け付ける.

    既存の confirmed / cooked / skipped 状態の日は上書きしない.
    今週以前の週 (week_start が今週月曜より前) は拒否する.
    """
    family_id = require_family(user)
    parsed = require_monday(parse_date(week_start))
    today = today_in_jst()
    current_monday = monday_of(today)
    if parsed < current_monday:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="過去週への提案リクエストはできません。",
        )

    client = get_firestore_client()

    # 対象日を判定 (上書き可能な状態のみ)
    target_dates: list[date] = []
    for offset in range(7):
        d = parsed + timedelta(days=offset)
        existing = get_meal_plan(client, family_id, d)
        if existing is None or can_overwrite(existing.status):
            target_dates.append(d)
    if not target_dates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="この週は全日確定済み・スキップ済みのため提案対象がありません。",
        )

    # weekly proposals プレースホルダ (scope=weekly)
    placeholder = pending_placeholder_record() | {
        "scope": "weekly",
        "week_start": parsed.isoformat(),
        "target_dates": [d.isoformat() for d in target_dates],
    }
    proposal_id = save_proposal_record(
        client,
        family_id=family_id,
        record=placeholder,
        requested_at=datetime.now(UTC),
    )

    # 対象日を一旦 status=proposed + dishes=() で予約
    for d in target_dates:
        upsert_meal_plan(
            client,
            MealPlan(
                family_id=family_id,
                plan_date=d,
                status="proposed",
                proposal_id=proposal_id,
                dishes=(),
                source="weekly_batch",
            ),
        )

    background_tasks.add_task(run_weekly_suggestion, family_id, parsed, proposal_id)

    return RedirectResponse(
        url=f"/plans/week/{parsed.isoformat()}/review",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/plans/week/{week_start}/review", response_class=HTMLResponse)
async def review_week(
    week_start: str,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    """週単位のレビューページ. 7 日分の提案を一画面で確認・確定する.

    week_start は月曜のみ受け付ける. 過去週も閲覧は可能だが, 確定や一括提案
    などの破壊的アクションはテンプレ側で抑制する (UI レベル).
    """
    family_id = require_family(user)
    parsed = require_monday(parse_date(week_start))

    today = today_in_jst()
    week_end = parsed + timedelta(days=6)
    client = get_firestore_client()

    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=parsed,
        end_date=week_end,
    )
    week = build_week_view(plans, week_start=parsed, today=today)

    proposals_by_date: dict[str, dict[str, object] | None] = {}
    seen_proposal_ids: dict[str, dict[str, object] | None] = {}
    for cell in week.days:
        plan = cell.meal_plan
        if plan is None or not plan.proposal_id:
            proposals_by_date[cell.iso] = None
            continue
        pid = plan.proposal_id
        if pid not in seen_proposal_ids:
            seen_proposal_ids[pid] = get_proposal(client, pid)
        proposals_by_date[cell.iso] = seen_proposal_ids[pid]

    current_monday = monday_of(today)
    pending_count = sum(
        1
        for cell in week.days
        if cell.meal_plan and cell.meal_plan.status == "proposed" and not cell.meal_plan.dishes
    )
    confirmable_count = sum(
        1
        for cell in week.days
        if cell.meal_plan and cell.meal_plan.status == "proposed" and cell.meal_plan.dishes
    )

    price_book = load_price_book()
    costs_by_date: dict[str, CostEstimate] = {
        plan.plan_date.isoformat(): estimate_meal_plan(plan, price_book)
        for plan in plans
        if plan.dishes and plan.status != "skipped"
    }
    week_cost = sum(costs_by_date.values(), CostEstimate())

    return templates.TemplateResponse(
        request,
        "plans/week_review.html",
        {
            "user": user,
            "today": today,
            "week": week,
            "proposals_by_date": proposals_by_date,
            "is_past_week": parsed < current_monday,
            "pending_count": pending_count,
            "confirmable_count": confirmable_count,
            "costs_by_date": costs_by_date,
            "week_cost": week_cost,
            "price_book": price_book,
        },
    )


@router.get("/plans/week/{week_start}/status")
async def week_status(
    week_start: str,
    user: AuthenticatedUser = Depends(current_user),
) -> dict[str, bool]:
    """生成完了ポーリング用. 生成中の日が 1 日でも残っていれば done: false を返す."""
    family_id = require_family(user)
    parsed = require_monday(parse_date(week_start))

    week_end = parsed + timedelta(days=6)
    client = get_firestore_client()
    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=parsed,
        end_date=week_end,
    )
    pending_count = sum(1 for plan in plans if plan.status == "proposed" and not plan.dishes)
    return {"done": pending_count == 0}


@router.post("/plans/week/{week_start}/confirm-all")
async def confirm_week(
    week_start: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    """週単位の一括確定. status=proposed かつ dishes 非空の日のみを確定する.

    pending (dishes 空) や既に confirmed/cooked/skipped の日は触らない.
    対象が 0 件でもエラーにせず, レビュー画面に戻す (UI でメッセージ表示).
    """
    family_id = require_family(user)
    parsed = require_monday(parse_date(week_start))
    today = today_in_jst()
    current_monday = monday_of(today)
    if parsed < current_monday:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="過去週は一括確定できません。",
        )

    client = get_firestore_client()
    week_end = parsed + timedelta(days=6)
    plans = list_meal_plans_in_range(
        client,
        family_id,
        start_date=parsed,
        end_date=week_end,
    )
    confirmed = 0
    for plan in plans:
        if plan.status == "proposed" and plan.dishes:
            update_status(client, family_id, plan.plan_date, status="confirmed")
            confirmed += 1

    logger.info(
        "weekly.confirm_all",
        family_id=family_id,
        week_start=parsed.isoformat(),
        confirmed_count=confirmed,
    )

    return RedirectResponse(
        url=f"/plans/week/{parsed.isoformat()}/review",
        status_code=status.HTTP_303_SEE_OTHER,
    )

"""提案詳細・採用・フィードバック (互換維持用).

新しい操作系は /plans/{date}/* に移行済み. このルーターは:
  - POST /proposals: 旧 URL を今日の /plans/{today}/propose にフォワード
  - GET /proposals/{id}: 過去提案の閲覧 (履歴等からの直リンク用)
  - GET /proposals/{id}/status: 生成完了ポーリング用の軽量 JSON
  - POST /proposals/{id}/accept: 採用 + 対応する meal_plan を cooked に更新
  - POST /proposals/{id}/feedback: フィードバック保存
の責務に絞る.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from recipe_system.observability.logging import get_logger
from recipe_system.repository.feedback_repository import save_feedback
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.meal_plan_repository import update_status
from recipe_system.repository.proposal_repository import (
    get_proposal,
    mark_accepted,
)
from recipe_system.services.calendar_view import today_in_jst
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()
logger = get_logger(__name__)


@router.post("/proposals")
async def create_proposal_legacy(
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    """旧トップから来たリクエストを今日の予定ページに振り直す."""
    require_family(user)
    today = today_in_jst()
    return RedirectResponse(
        url=f"/plans/{today.isoformat()}/propose",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def show_proposal(
    proposal_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    data = get_proposal(client, proposal_id)
    if data is None or data.get("family_id") != family_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提案が見つかりません")

    status_value = data.get("status", "ready")
    if status_value == "pending":
        return templates.TemplateResponse(
            request,
            "proposals/pending.html",
            {"proposal_id": proposal_id, "user": user},
        )

    return templates.TemplateResponse(
        request,
        "proposals/detail.html",
        {"proposal": data, "proposal_id": proposal_id, "user": user},
    )


@router.get("/proposals/{proposal_id}/status")
async def proposal_status(
    proposal_id: str,
    user: AuthenticatedUser = Depends(current_user),
) -> dict[str, bool]:
    """生成完了ポーリング用. pending なら done: false を返す."""
    family_id = require_family(user)
    client = get_firestore_client()
    data = get_proposal(client, proposal_id)
    if data is None or data.get("family_id") != family_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提案が見つかりません")

    return {"done": data.get("status", "ready") != "pending"}


@router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    data = get_proposal(client, proposal_id)
    if data is None or data.get("family_id") != family_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    mark_accepted(client, proposal_id, True)

    plan_date_str = data.get("plan_date")
    if plan_date_str:
        try:
            plan_date = date.fromisoformat(plan_date_str)
        except ValueError:
            plan_date = None
        if plan_date is not None:
            update_status(client, family_id, plan_date, status="cooked")
            return RedirectResponse(
                url=f"/plans/{plan_date.isoformat()}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    return RedirectResponse(
        url=f"/proposals/{proposal_id}?accepted=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/proposals/{proposal_id}/feedback")
async def submit_feedback(
    proposal_id: str,
    request: Request,
    rating: str = Form(...),
    comment: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    if rating not in ("good", "neutral", "pass"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="無効な rating")
    client = get_firestore_client()
    data = get_proposal(client, proposal_id)
    if data is None or data.get("family_id") != family_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    save_feedback(
        client,
        family_id=family_id,
        proposal_id=proposal_id,
        rating=rating,  # type: ignore[arg-type]
        comment=comment,
    )
    return templates.TemplateResponse(
        request,
        "shared/feedback_sent.html",
        {
            "user": user,
            "heading": "フィードバックを受け付けました",
            "message": "今後の提案品質向上に活用します。",
            "back_url": "/",
            "back_label": "トップに戻る",
            "secondary_url": f"/proposals/{proposal_id}",
            "secondary_label": "提案に戻る",
        },
    )

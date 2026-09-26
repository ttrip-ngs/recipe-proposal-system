"""履歴閲覧."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.history_repository import recent_history
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
async def show_history(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    entries = recent_history(client, family_id, days=30)
    return templates.TemplateResponse(
        request, "history/list.html", {"entries": entries, "user": user}
    )

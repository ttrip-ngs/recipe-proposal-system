"""家族プロファイル閲覧 (編集は Phase 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from recipe_system.guardrails.dictionary_loader import default_dictionary
from recipe_system.guardrails.validators import allowed_items, undecided_items
from recipe_system.repository.family_repository import FamilyNotFoundError, get_family
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
async def show_profile(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> HTMLResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    try:
        family = get_family(client, family_id)
    except FamilyNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    dic = default_dictionary()
    return templates.TemplateResponse(
        request,
        "profile/show.html",
        {
            "family": family,
            "user": user,
            "allowed": {m.member_id: sorted(allowed_items(m, dic)) for m in family.members},
            "undecided": {m.member_id: undecided_items(m, dic) for m in family.members},
        },
    )

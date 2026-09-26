"""家族プロファイル編集 (Phase D)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from recipe_system.guardrails.dictionary_loader import default_dictionary
from recipe_system.repository.family_repository import (
    FamilyNotFoundError,
    MemberNotFoundError,
    delete_member,
    get_family,
    update_family_meta,
    upsert_member,
)
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user, require_family
from recipe_system.web.templating import templates

router = APIRouter()


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.replace("\n", ",").split(",") if s.strip()]


@router.get("/profile/edit", response_class=HTMLResponse)
async def show_edit(
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
    allergen_groups = [
        {"name": name, "members": sorted(members)}
        for name, members in dic.allergen_group_members.items()
    ]

    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {
            "user": user,
            "family": family,
            "allergen_groups": allergen_groups,
        },
    )


@router.post("/profile/members/{member_id}")
async def update_member(
    member_id: str,
    name: str = Form(...),
    role: str | None = Form(default=None),
    allergens: list[str] = Form(default=[]),
    dislikes_csv: str | None = Form(default=None),
    likes_csv: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    if not name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="名前は必須です。")
    upsert_member(
        client,
        family_id,
        member_id=member_id,
        name=name.strip(),
        role=(role.strip() or None) if role else None,
        allergens=allergens,
        dislikes=_split_csv(dislikes_csv),
        likes=_split_csv(likes_csv),
        notes=(notes.strip() or None) if notes else None,
    )
    return RedirectResponse(url="/profile/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/members")
async def add_member(
    name: str = Form(...),
    role: str | None = Form(default=None),
    allergens: list[str] = Form(default=[]),
    dislikes_csv: str | None = Form(default=None),
    likes_csv: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    if not name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="名前は必須です。")
    upsert_member(
        client,
        family_id,
        name=name.strip(),
        role=(role.strip() or None) if role else None,
        allergens=allergens,
        dislikes=_split_csv(dislikes_csv),
        likes=_split_csv(likes_csv),
        notes=(notes.strip() or None) if notes else None,
    )
    return RedirectResponse(url="/profile/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/members/{member_id}/delete")
async def delete_member_route(
    member_id: str,
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    try:
        delete_member(client, family_id, member_id)
    except MemberNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return RedirectResponse(url="/profile/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/meta")
async def update_meta_route(
    name: str = Form(...),
    allowed_emails_csv: str = Form(...),
    user: AuthenticatedUser = Depends(current_user),
) -> RedirectResponse:
    family_id = require_family(user)
    client = get_firestore_client()
    if not name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="家族名は必須です。")
    emails = _split_csv(allowed_emails_csv)
    if not emails:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="allowed_emails は最低 1 件必須です。",
        )
    update_family_meta(
        client,
        family_id,
        name=name.strip(),
        allowed_emails=emails,
    )
    return RedirectResponse(url="/profile/edit", status_code=status.HTTP_303_SEE_OTHER)

"""家族プロファイル編集 (Phase D)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import FormData

from recipe_system.domain import ItemPolicy
from recipe_system.guardrails.dictionary_loader import default_dictionary
from recipe_system.guardrails.validators import undecided_items
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

_POLICY_VALUES: dict[str, ItemPolicy] = {"allow": "allow", "block": "block"}


def _parse_item_policies(form: FormData, allergens: list[str]) -> dict[str, dict[str, ItemPolicy]]:
    """選んだアレルゲンの「通常は除去不要な食品」ごとの可/不可をフォームから読む.

    選択は必須 (ADR 0007). 1 つでも未選択なら保存しない. 画面側の required は補助で,
    ここが正本の検証.
    """
    dic = default_dictionary()
    policies: dict[str, dict[str, ItemPolicy]] = {}
    missing: list[str] = []
    for allergen in allergens:
        for item in sorted(dic.tolerable_items(allergen)):
            policy = _POLICY_VALUES.get(str(form.get(f"policy__{allergen}__{item}")))
            if policy is None:
                missing.append(f"{allergen}の{item}")
            else:
                policies.setdefault(allergen, {})[item] = policy
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"次の食品を食べてよいか (可/不可) を選んでください: {', '.join(missing)}",
        )
    return policies


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
    allergen_groups = []
    for name, members in dic.allergen_group_members.items():
        # 通常は除去不要な食品 (醤油など) はアレルゲンとしては選ばせず, 可/不可の選択肢にする
        selectable = sorted(members - dic.allergen_group_tolerated.get(name, frozenset()))
        allergen_groups.append(
            {
                "name": name,
                "members": selectable,
                "policy_items": {
                    c: sorted(dic.tolerable_items(c)) for c in selectable if dic.tolerable_items(c)
                },
            }
        )

    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {
            "user": user,
            "family": family,
            "allergen_groups": allergen_groups,
            "undecided": {m.member_id: undecided_items(m, dic) for m in family.members},
        },
    )


@router.post("/profile/members/{member_id}")
async def update_member(
    request: Request,
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
    item_policies = _parse_item_policies(await request.form(), allergens)
    upsert_member(
        client,
        family_id,
        member_id=member_id,
        name=name.strip(),
        role=(role.strip() or None) if role else None,
        allergens=allergens,
        item_policies=item_policies,
        dislikes=_split_csv(dislikes_csv),
        likes=_split_csv(likes_csv),
        notes=(notes.strip() or None) if notes else None,
    )
    return RedirectResponse(url="/profile/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/members")
async def add_member(
    request: Request,
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
    item_policies = _parse_item_policies(await request.form(), allergens)
    upsert_member(
        client,
        family_id,
        name=name.strip(),
        role=(role.strip() or None) if role else None,
        allergens=allergens,
        item_policies=item_policies,
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

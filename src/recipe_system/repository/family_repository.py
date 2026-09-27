"""families コレクションのアクセス層."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from recipe_system.domain import FamilyMember, FamilyProfile, ItemPolicy
from recipe_system.repository.converters import to_datetime


class FamilyNotFoundError(LookupError):
    pass


class MemberNotFoundError(LookupError):
    pass


def get_family(client: firestore.Client, family_id: str) -> FamilyProfile:
    doc = client.collection("families").document(family_id).get()
    if not doc.exists:
        raise FamilyNotFoundError(f"family_id={family_id}")
    data = doc.to_dict() or {}

    members_docs = client.collection("families").document(family_id).collection("members").stream()
    members: list[FamilyMember] = []
    for m in members_docs:
        md = m.to_dict() or {}
        members.append(
            FamilyMember(
                member_id=m.id,
                name=md["name"],
                role=md.get("role"),
                allergens=frozenset(md.get("allergens", [])),
                item_policies=md.get("item_policies", {}),
                dislikes=frozenset(md.get("dislikes", [])),
                likes=frozenset(md.get("likes", [])),
                notes=md.get("notes"),
                reviewed_at=to_datetime(md.get("reviewed_at")),
            )
        )

    return FamilyProfile(
        family_id=family_id,
        name=data["name"],
        timezone=data.get("timezone", "Asia/Tokyo"),
        allowed_emails=frozenset(data.get("allowed_emails", [])),
        members=tuple(members),
        created_at=to_datetime(data.get("created_at")),
        updated_at=to_datetime(data.get("updated_at")),
    )


def find_family_id_by_email(client: firestore.Client, email: str) -> str | None:
    docs = (
        client.collection("families")
        .where(filter=FieldFilter("allowed_emails", "array_contains", email))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id
    return None


def upsert_member(
    client: firestore.Client,
    family_id: str,
    *,
    member_id: str | None = None,
    name: str,
    role: str | None = None,
    allergens: frozenset[str] | list[str] = (),
    item_policies: Mapping[str, Mapping[str, ItemPolicy]] | None = None,
    dislikes: frozenset[str] | list[str] = (),
    likes: frozenset[str] | list[str] = (),
    notes: str | None = None,
) -> str:
    """メンバー追加/更新. member_id 未指定なら新規作成.

    reviewed_at は呼び出しのたびに現在時刻にセット (家族プロファイル確認のタイムスタンプ).
    """
    now = datetime.now(UTC)
    mid = member_id or f"m-{uuid4().hex[:8]}"
    payload: dict[str, Any] = {
        "name": name,
        "role": role,
        "allergens": sorted(allergens),
        "item_policies": {a: dict(p) for a, p in (item_policies or {}).items()},
        "dislikes": sorted(dislikes),
        "likes": sorted(likes),
        "notes": notes,
        "reviewed_at": now,
    }
    (
        client.collection("families")
        .document(family_id)
        .collection("members")
        .document(mid)
        # merge=True は入れ子の map を再帰的にマージし, 外したアレルギーの item_policies が
        # 残る. 書き込むフィールドを列挙し, 各フィールドは丸ごと置き換える
        .set(payload, merge=list(payload))
    )
    _touch_family_updated_at(client, family_id, now)
    return mid


def delete_member(client: firestore.Client, family_id: str, member_id: str) -> None:
    ref = (
        client.collection("families").document(family_id).collection("members").document(member_id)
    )
    if not ref.get().exists:
        raise MemberNotFoundError(f"family_id={family_id} member_id={member_id}")
    ref.delete()
    _touch_family_updated_at(client, family_id, datetime.now(UTC))


def update_family_meta(
    client: firestore.Client,
    family_id: str,
    *,
    name: str | None = None,
    timezone: str | None = None,
    allowed_emails: frozenset[str] | list[str] | None = None,
) -> None:
    now = datetime.now(UTC)
    patch: dict[str, Any] = {"updated_at": now}
    if name is not None:
        patch["name"] = name
    if timezone is not None:
        patch["timezone"] = timezone
    if allowed_emails is not None:
        patch["allowed_emails"] = sorted(allowed_emails)
    client.collection("families").document(family_id).set(patch, merge=True)


def _touch_family_updated_at(client: firestore.Client, family_id: str, now: datetime) -> None:
    client.collection("families").document(family_id).set({"updated_at": now}, merge=True)

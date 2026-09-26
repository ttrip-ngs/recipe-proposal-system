"""family_repository.upsert_member / delete_member / update_family_meta の統合テスト."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _setup_family(family_id: str) -> None:
    from datetime import UTC, datetime

    from google.cloud import firestore

    client = firestore.Client(project="recipe-system-dev")
    now = datetime.now(UTC)
    client.collection("families").document(family_id).set(
        {
            "name": "プロファイル編集テスト家",
            "timezone": "Asia/Tokyo",
            "allowed_emails": ["original@example.com"],
            "created_at": now,
            "updated_at": now,
        }
    )


def _cleanup_family(family_id: str) -> None:
    from google.cloud import firestore

    client = firestore.Client(project="recipe-system-dev")
    members = client.collection("families").document(family_id).collection("members").stream()
    for m in members:
        m.reference.delete()
    client.collection("families").document(family_id).delete()


def test_upsert_member_新規追加と更新() -> None:
    from recipe_system.repository.family_repository import get_family, upsert_member
    from recipe_system.repository.firestore_client import get_firestore_client

    family_id = "integration-test-profile-edit"
    _cleanup_family(family_id)
    _setup_family(family_id)

    client = get_firestore_client()

    # 新規追加 (member_id 未指定 → 自動生成)
    mid = upsert_member(
        client,
        family_id,
        name="夫",
        role="adult",
        allergens=["甲殻類"],
        dislikes=["ピーマン"],
    )
    assert mid.startswith("m-")

    family = get_family(client, family_id)
    husband = next(m for m in family.members if m.name == "夫")
    assert "甲殻類" in husband.allergens
    assert "ピーマン" in husband.dislikes

    # 更新 (同じ member_id を指定)
    upsert_member(
        client,
        family_id,
        member_id=mid,
        name="夫",
        role="adult",
        allergens=["甲殻類", "卵"],
        dislikes=[],
    )
    family = get_family(client, family_id)
    husband = next(m for m in family.members if m.name == "夫")
    assert husband.allergens == frozenset({"甲殻類", "卵"})
    assert husband.dislikes == frozenset()

    _cleanup_family(family_id)


def test_delete_member() -> None:
    from recipe_system.repository.family_repository import (
        MemberNotFoundError,
        delete_member,
        get_family,
        upsert_member,
    )
    from recipe_system.repository.firestore_client import get_firestore_client

    family_id = "integration-test-profile-delete"
    _cleanup_family(family_id)
    _setup_family(family_id)

    client = get_firestore_client()
    mid = upsert_member(client, family_id, name="子")

    delete_member(client, family_id, mid)
    family = get_family(client, family_id)
    assert all(m.member_id != mid for m in family.members)

    # 再度削除は MemberNotFoundError
    with pytest.raises(MemberNotFoundError):
        delete_member(client, family_id, mid)

    _cleanup_family(family_id)


def test_update_family_meta() -> None:
    from recipe_system.repository.family_repository import (
        get_family,
        update_family_meta,
    )
    from recipe_system.repository.firestore_client import get_firestore_client

    family_id = "integration-test-profile-meta"
    _cleanup_family(family_id)
    _setup_family(family_id)

    client = get_firestore_client()
    update_family_meta(
        client,
        family_id,
        name="名前変更家",
        allowed_emails=["a@example.com", "b@example.com"],
    )
    family = get_family(client, family_id)
    assert family.name == "名前変更家"
    assert family.allowed_emails == frozenset({"a@example.com", "b@example.com"})

    _cleanup_family(family_id)

"""Firestore エミュレータ経由で repository 層を疎通確認する統合テスト.

実行前に `docker compose up -d firebase-emulators` が必要.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration


def test_family_の書き込みと読み出し() -> None:
    from google.cloud import firestore

    from recipe_system.repository.family_repository import get_family

    client = firestore.Client(project="recipe-system-dev")
    now = datetime.now(UTC)
    family_id = "integration-test-family"
    client.collection("families").document(family_id).set(
        {
            "name": "統合テスト家",
            "timezone": "Asia/Tokyo",
            "allowed_emails": ["it@example.com"],
            "created_at": now,
            "updated_at": now,
        }
    )
    client.collection("families").document(family_id).collection("members").document("m-001").set(
        {
            "name": "テスト夫",
            "role": "adult",
            "allergens": ["甲殻類"],
            "dislikes": [],
            "likes": [],
            "notes": None,
            "reviewed_at": now,
        }
    )

    family = get_family(client, family_id)
    assert family.name == "統合テスト家"
    assert family.members[0].name == "テスト夫"
    assert "甲殻類" in family.members[0].allergens


def test_recipe_の登録() -> None:
    from google.cloud import firestore

    from recipe_system.repository.recipe_repository import list_recipes

    client = firestore.Client(project="recipe-system-dev")
    now = datetime.now(UTC)
    client.collection("recipes").add(
        {
            "name": "統合テスト肉じゃが",
            "category": "主菜",
            "main_ingredient": "牛肉",
            "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
            "steps": [],
            "tags": ["和食"],
            "servings": 4,
            "source": None,
            "created_at": now,
        }
    )
    recipes = list_recipes(client)
    names = {r.name for r in recipes}
    assert "統合テスト肉じゃが" in names

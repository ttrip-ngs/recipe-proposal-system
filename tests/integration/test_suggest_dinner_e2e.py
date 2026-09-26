"""Firestore エミュレータ + フェイク LLM で三段階処理を end-to-end で通すテスト.

scripts/seed_firestore.py --emulator で投入した example-family / example recipes を前提とする.
依存のない形で seed を冪等に行うため、本テスト内で seed も実行する.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from google.cloud import firestore

from recipe_system.llm.client import FakeVertexClient
from recipe_system.repository.family_repository import get_family
from recipe_system.repository.history_repository import recent_history
from recipe_system.repository.proposal_repository import (
    get_proposal,
    pending_placeholder_record,
    save_proposal_record,
    update_proposal_record,
)
from recipe_system.repository.recipe_repository import list_recipes
from recipe_system.services.suggest_dinner import SuggestContext, suggest_dinner


@pytest.fixture
def seeded_client() -> firestore.Client:
    """Firestore エミュレータに最小限のデータを投入して Client を返す."""
    client = firestore.Client(project="recipe-system-dev")
    now = datetime.now(UTC)

    client.collection("families").document("e2e-family").set(
        {
            "name": "E2E 家",
            "timezone": "Asia/Tokyo",
            "allowed_emails": ["e2e@example.com"],
            "created_at": now,
            "updated_at": now,
        }
    )
    client.collection("families").document("e2e-family").collection("members").document(
        "m-wife"
    ).set(
        {
            "name": "妻",
            "role": "adult",
            "allergens": ["甲殻類"],
            "dislikes": [],
            "likes": [],
            "notes": None,
            "reviewed_at": now,
        }
    )
    client.collection("families").document("e2e-family").collection("members").document(
        "m-husband"
    ).set(
        {
            "name": "夫",
            "role": "adult",
            "allergens": [],
            "dislikes": ["セロリ"],
            "likes": [],
            "notes": None,
            "reviewed_at": now,
        }
    )
    # レシピは recipes コレクションに冪等に add (既存があっても追加される形で OK)
    client.collection("recipes").add(
        {
            "name": "E2E 牛丼",
            "category": "主菜",
            "main_ingredient": "牛肉",
            "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
            "steps": [],
            "tags": [],
            "servings": 4,
            "source": None,
            "created_at": now,
        }
    )
    return client


def test_suggest_dinner_e2e(seeded_client: firestore.Client) -> None:
    family = get_family(seeded_client, "e2e-family")
    assert family.name == "E2E 家"

    recipes = list_recipes(seeded_client)
    assert len(recipes) >= 1

    history = recent_history(seeded_client, "e2e-family", days=14)
    # 履歴は空でよい

    ctx = SuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=history,
        pantry=("牛肉", "じゃがいも"),
        user_request="今晩の献立",
    )

    proposal = asyncio.run(suggest_dinner(ctx, llm=FakeVertexClient()))

    assert proposal.succeeded is True, proposal.violations
    # フェイククライアントの固定応答は 3 品 (主菜・副菜・汁物)
    categories = {d.category for d in proposal.dishes}
    assert categories == {"主菜", "副菜", "汁物"}

    # 甲殻類 (妻のアレルゲン) が含まれていないこと
    for dish in proposal.dishes:
        for ing in dish.ingredients:
            assert "甲殻類" not in ing.allergen_tags, f"block 漏れ: {ing.name}"


def test_proposal_record_の書き込みと読み出し(seeded_client: firestore.Client) -> None:
    """proposal_repository の save -> update -> get 経路を確認."""
    proposal_id = save_proposal_record(
        seeded_client,
        family_id="e2e-family",
        record=pending_placeholder_record(),
    )
    fetched = get_proposal(seeded_client, proposal_id)
    assert fetched is not None
    assert fetched["status"] == "pending"
    assert fetched["family_id"] == "e2e-family"

    update_proposal_record(
        seeded_client,
        proposal_id,
        {
            "status": "ready",
            "succeeded": True,
            "dishes": [{"name": "牛丼", "category": "主菜"}],
        },
    )
    fetched = get_proposal(seeded_client, proposal_id)
    assert fetched is not None
    assert fetched["status"] == "ready"
    assert fetched["succeeded"] is True
    assert fetched["dishes"][0]["name"] == "牛丼"

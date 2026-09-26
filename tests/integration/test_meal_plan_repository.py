"""meal_plan_repository の Firestore エミュレータ統合テスト."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.integration


def test_upsert_get_list_range_往復() -> None:
    from google.cloud import firestore

    from recipe_system.domain import MealPlan, MealPlanDish
    from recipe_system.repository.meal_plan_repository import (
        delete_meal_plan,
        get_meal_plan,
        list_meal_plans_in_range,
        update_status,
        upsert_meal_plan,
    )

    client = firestore.Client(project="recipe-system-dev")
    family_id = "integration-test-meal-plan"

    # 既存データを掃除
    for plan_date in (date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27)):
        delete_meal_plan(client, family_id, plan_date)

    # 新規 upsert
    plan = MealPlan(
        family_id=family_id,
        plan_date=date(2026, 5, 25),
        status="proposed",
        proposal_id="prop-xyz",
        dishes=(
            MealPlanDish(
                name="肉じゃが",
                category="主菜",
                main_ingredient="牛肉",
                reason="春の定番",
                ingredients=({"name": "牛肉", "quantity": 300, "unit": "g"},),
                steps=("牛肉と野菜を切る", "煮汁で柔らかくなるまで煮る"),
            ),
        ),
        source="single",
    )
    doc_id = upsert_meal_plan(client, plan)
    assert doc_id == f"{family_id}_2026-05-25"

    # 取得
    got = get_meal_plan(client, family_id, date(2026, 5, 25))
    assert got is not None
    assert got.status == "proposed"
    assert got.dishes[0].name == "肉じゃが"
    assert got.dishes[0].steps == ("牛肉と野菜を切る", "煮汁で柔らかくなるまで煮る")
    assert got.proposal_id == "prop-xyz"
    assert isinstance(got.created_at, datetime)
    assert got.created_at.tzinfo is not None

    # 部分更新で cooked に
    update_status(client, family_id, date(2026, 5, 25), status="cooked")
    after = get_meal_plan(client, family_id, date(2026, 5, 25))
    assert after is not None
    assert after.status == "cooked"
    # dishes は維持される
    assert after.dishes[0].name == "肉じゃが"

    # 別日を作って範囲取得
    update_status(
        client,
        family_id,
        date(2026, 5, 27),
        status="confirmed",
        source="manual",
    )
    in_range = list_meal_plans_in_range(
        client,
        family_id,
        start_date=date(2026, 5, 25),
        end_date=date(2026, 5, 27),
    )
    plan_dates = [p.plan_date for p in in_range]
    assert date(2026, 5, 25) in plan_dates
    assert date(2026, 5, 27) in plan_dates
    # 順序確認 (昇順)
    assert plan_dates == sorted(plan_dates)

    # クリーンアップ
    for plan_date in (date(2026, 5, 25), date(2026, 5, 27)):
        delete_meal_plan(client, family_id, plan_date)

    for d in (22, 23):
        delete_meal_plan(client, family_id, date(2026, 5, d))


def test_時刻_tzinfo_が付与されて返る() -> None:
    from google.cloud import firestore

    from recipe_system.domain import MealPlan
    from recipe_system.repository.meal_plan_repository import (
        delete_meal_plan,
        get_meal_plan,
        upsert_meal_plan,
    )

    client = firestore.Client(project="recipe-system-dev")
    family_id = "integration-test-tz"

    delete_meal_plan(client, family_id, date(2026, 6, 1))
    upsert_meal_plan(
        client,
        MealPlan(
            family_id=family_id,
            plan_date=date(2026, 6, 1),
            status="empty",
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        ),
    )
    got = get_meal_plan(client, family_id, date(2026, 6, 1))
    assert got is not None
    assert got.updated_at is not None
    assert got.updated_at.tzinfo is not None
    delete_meal_plan(client, family_id, date(2026, 6, 1))

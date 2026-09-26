"""domain.meal_plan のユニットテスト."""

from __future__ import annotations

from datetime import date

from recipe_system.domain import MealPlan, MealPlanDish, make_plan_id


def test_plan_id_は_family_id_と_plan_date_の組み合わせ() -> None:
    plan = MealPlan(family_id="fam-1", plan_date=date(2026, 5, 25))
    assert plan.plan_id == "fam-1_2026-05-25"
    assert make_plan_id("fam-1", date(2026, 5, 25)) == "fam-1_2026-05-25"


def test_デフォルトは_empty_dinner_manual() -> None:
    plan = MealPlan(family_id="fam-1", plan_date=date(2026, 5, 25))
    assert plan.status == "empty"
    assert plan.slot == "dinner"
    assert plan.source == "manual"
    assert plan.dishes == ()
    assert plan.proposal_id is None


def test_dishes_を_持つ_proposed_状態() -> None:
    dish = MealPlanDish(
        name="肉じゃが",
        category="主菜",
        main_ingredient="牛肉",
        reason="季節の定番",
        ingredients=({"name": "牛肉", "quantity": 300, "unit": "g"},),
    )
    plan = MealPlan(
        family_id="fam-1",
        plan_date=date(2026, 5, 25),
        status="proposed",
        proposal_id="prop-1",
        dishes=(dish,),
        source="single",
    )
    assert plan.status == "proposed"
    assert plan.proposal_id == "prop-1"
    assert len(plan.dishes) == 1
    assert plan.dishes[0].name == "肉じゃが"


def test_手順導入前に保存された料理は_steps_が空で読める() -> None:
    """2026-09 の手順導入前の Firestore ドキュメントには steps が無い."""
    from recipe_system.domain import MealPlanDish

    dish = MealPlanDish(**{"name": "肉じゃが", "category": "主菜", "main_ingredient": "牛肉"})
    assert dish.steps == ()

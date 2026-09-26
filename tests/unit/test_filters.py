"""事前フィルタ (services.filters) の単体テスト."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from recipe_system.domain import FamilyMember, Ingredient, Recipe
from recipe_system.services.filters import prefilter_recipes, recent_recipe_names


def _member(name: str, allergens: set[str] | None = None) -> FamilyMember:
    return FamilyMember(
        member_id=f"m-{name}",
        name=name,
        allergens=frozenset(allergens or set()),
        reviewed_at=datetime.now(UTC),
    )


def _recipe(name: str, ingredients: list[Ingredient]) -> Recipe:
    return Recipe(
        name=name,
        category="主菜",
        main_ingredient=ingredients[0].canonical,
        ingredients=tuple(ingredients),
    )


def test_アレルゲン含有レシピが除外される() -> None:
    family = [_member("妻", {"甲殻類"})]
    recipes = [
        _recipe(
            "エビチリ",
            [Ingredient(name="エビ", canonical="エビ", allergen_tags=frozenset({"甲殻類"}))],
        ),
        _recipe(
            "牛丼", [Ingredient(name="牛肉", canonical="牛肉", allergen_tags=frozenset({"肉類"}))]
        ),
    ]
    filtered, stats = prefilter_recipes(recipes, family, recent_recipe_names=[])
    names = {r.name for r in filtered}
    assert names == {"牛丼"}
    assert stats.excluded_by_allergen == 1
    assert stats.excluded_by_recency == 0


def test_直近履歴の料理名が除外される() -> None:
    family = [_member("夫")]
    recipes = [
        _recipe("肉じゃが", [Ingredient(name="牛肉", canonical="牛肉", allergen_tags=frozenset())]),
        _recipe(
            "鶏の照り焼き", [Ingredient(name="鶏肉", canonical="鶏肉", allergen_tags=frozenset())]
        ),
    ]
    filtered, stats = prefilter_recipes(recipes, family, recent_recipe_names=["肉じゃが"])
    assert {r.name for r in filtered} == {"鶏の照り焼き"}
    assert stats.excluded_by_recency == 1


def test_recent_recipe_names_で期間内のみ返る() -> None:
    now = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
    history = [
        ("A", now - timedelta(days=1)),  # 含む
        ("B", now - timedelta(days=8)),  # 除外
        ("C", now - timedelta(days=6, hours=23)),  # 含む
    ]
    names = recent_recipe_names(history, recency_days=7, now=now)
    assert set(names) == {"A", "C"}


def test_アレルゲンがなければ全件残る() -> None:
    family = [_member("夫")]
    recipes = [
        _recipe("肉じゃが", [Ingredient(name="牛肉", canonical="牛肉", allergen_tags=frozenset())]),
    ]
    filtered, stats = prefilter_recipes(recipes, family, recent_recipe_names=[])
    assert len(filtered) == 1
    assert stats.total == 1
    assert stats.after_recency == 1

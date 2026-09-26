"""買い物用食材辞書 (services/shopping_dictionary.yaml) のテスト.

アレルゲン判定には使わないが, 辞書変更時のテスト追加ルール (CLAUDE.md 6.2) に合わせてここに置く.
"""

from __future__ import annotations

from datetime import date

from recipe_system.domain import MealPlan, MealPlanDish
from recipe_system.services.shopping_aggregator import aggregate_week
from recipe_system.services.shopping_dictionary import ShoppingKind, get_shopping_dictionary


def test_しょうゆ表記は醤油の常備品として引ける() -> None:
    dic = get_shopping_dictionary()
    for raw in ["しょうゆ", "ショウユ", "醤油", "しょう油", "濃口醤油"]:
        entry = dic.lookup(raw)
        assert entry.kind is ShoppingKind.PANTRY, raw
        assert entry.name == "醤油", raw


def test_薄口醤油は醤油と別扱い() -> None:
    assert get_shopping_dictionary().lookup("薄口醤油").name == "薄口醤油"


def test_醤油としょうゆは買い物リストで1行にまとまる() -> None:
    plan = MealPlan(
        family_id="fam-1",
        plan_date=date(2026, 5, 25),
        status="confirmed",
        dishes=(
            MealPlanDish(
                name="料理",
                category="主菜",
                main_ingredient="豚肉",
                ingredients=(
                    {"name": "醤油", "quantity": 1, "unit": "大さじ"},
                    {"name": "しょうゆ", "quantity": 2, "unit": "小さじ"},
                ),
            ),
        ),
        source="single",
    )
    sl = aggregate_week([plan], family_id="fam-1", week_start=date(2026, 5, 25))
    assert len(sl.items) == 1
    item = sl.items[0]
    assert item.canonical == "醤油"
    assert item.pantry is True
    assert [(a.quantity, a.unit) for a in item.amounts] == [(25.0, "ml")]
    assert set(item.raw_names) == {"醤油", "しょうゆ"}

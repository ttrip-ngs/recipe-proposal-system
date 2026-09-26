"""repository.shopping_list_repository の変換処理のユニットテスト."""

from __future__ import annotations

from datetime import date

from recipe_system.domain import ShoppingAmount, ShoppingItem, ShoppingList
from recipe_system.repository.shopping_list_repository import _to_model, _to_record


def _record(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "family_id": "fam-1",
        "week_start": "2026-05-25",
        "week_end": "2026-05-31",
        "items": items,
    }


def test_旧形式の_total_quantity_unit_を_amounts_として読める() -> None:
    sl = _to_model(
        _record(
            [
                {"item_id": "a", "canonical": "牛肉", "total_quantity": 300.0, "unit": "g"},
                {"item_id": "b", "canonical": "こしょう", "total_quantity": None, "unit": None},
            ]
        )
    )
    beef, pepper = sl.items
    assert beef.amounts == (ShoppingAmount(quantity=300.0, unit="g"),)
    assert beef.pantry is False
    assert pepper.amounts == ()


def test_新形式は_amounts_と_pantry_を往復できる() -> None:
    original = ShoppingList(
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        week_end=date(2026, 5, 31),
        items=(
            ShoppingItem(
                item_id="x",
                canonical="にんにく",
                amounts=(
                    ShoppingAmount(quantity=8, unit="g"),
                    ShoppingAmount(quantity=1, unit="片"),
                ),
            ),
            ShoppingItem(item_id="y", canonical="醤油", pantry=True),
        ),
    )
    restored = _to_model(_to_record(original))
    assert restored.items == original.items

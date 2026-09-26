"""domain.shopping_list のユニットテスト."""

from __future__ import annotations

from datetime import date

from recipe_system.domain import (
    ShoppingAmount,
    ShoppingItem,
    ShoppingList,
    iso_week_label,
    make_shopping_list_id,
)


def test_iso_week_label_は_YYYY_Www_形式() -> None:
    # 2026-05-25 は月曜, ISO 週は 2026-W22
    assert iso_week_label(date(2026, 5, 25)) == "2026-W22"


def test_shopping_list_id_は_family_id_と_iso_week_の組み合わせ() -> None:
    assert make_shopping_list_id("fam-1", date(2026, 5, 25)) == "fam-1_2026-W22"
    sl = ShoppingList(
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        week_end=date(2026, 5, 31),
    )
    assert sl.list_id == "fam-1_2026-W22"


def test_remaining_count_は_未チェック数() -> None:
    sl = ShoppingList(
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        week_end=date(2026, 5, 31),
        items=(
            ShoppingItem(canonical="牛肉", amounts=(ShoppingAmount(quantity=300, unit="g"),)),
            ShoppingItem(
                canonical="豚肉", amounts=(ShoppingAmount(quantity=250, unit="g"),), checked=True
            ),
            ShoppingItem(canonical="にんじん", amounts=(ShoppingAmount(quantity=2, unit="本"),)),
        ),
    )
    assert sl.total_count == 3
    assert sl.remaining_count == 2


def test_常備品は_件数に数えず_別枠で取り出せる() -> None:
    sl = ShoppingList(
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        week_end=date(2026, 5, 31),
        items=(
            ShoppingItem(canonical="牛肉"),
            ShoppingItem(canonical="醤油", pantry=True),
            ShoppingItem(canonical="塩", pantry=True, checked=True),
        ),
    )
    assert [it.canonical for it in sl.buy_items] == ["牛肉"]
    assert [it.canonical for it in sl.pantry_items] == ["醤油", "塩"]
    assert sl.total_count == 1
    assert sl.remaining_count == 1


def test_amount_text_は単位ごとに並記する() -> None:
    item = ShoppingItem(
        canonical="にんにく",
        amounts=(ShoppingAmount(quantity=8.0, unit="g"), ShoppingAmount(quantity=1.5, unit="片")),
    )
    assert item.amount_text == "8g + 1.5片"


def test_shopping_item_id_は_自動生成される() -> None:
    item1 = ShoppingItem(canonical="牛肉")
    item2 = ShoppingItem(canonical="牛肉")
    assert item1.item_id != item2.item_id
    assert len(item1.item_id) > 0

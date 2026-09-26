"""services.shopping_aggregator のユニットテスト."""

from __future__ import annotations

from datetime import date

from recipe_system.domain import MealPlan, MealPlanDish, ShoppingAmount, ShoppingItem
from recipe_system.services.shopping_aggregator import aggregate_week


def _plan(d: date, *, status: str = "confirmed", ingredients_per_dish=None) -> MealPlan:
    dishes = []
    for i, ings in enumerate(ingredients_per_dish or [[]]):
        dishes.append(
            MealPlanDish(
                name=f"料理{i}",
                category="主菜",
                main_ingredient=ings[0].get("canonical", ings[0]["name"]) if ings else "不明",
                ingredients=tuple(ings),
            )
        )
    return MealPlan(
        family_id="fam-1",
        plan_date=d,
        status=status,  # type: ignore[arg-type]
        dishes=tuple(dishes),
        source="single",
    )


def _amounts(item: ShoppingItem) -> dict[str | None, float]:
    return {a.unit: a.quantity for a in item.amounts}


def test_confirmed_と_cooked_だけ集約対象() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            status="cooked",
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 300, "unit": "g"}]
            ],
        ),
        _plan(
            date(2026, 5, 26),
            status="proposed",  # 集約対象外
            ingredients_per_dish=[
                [{"canonical": "豚肉", "name": "豚肉", "quantity": 200, "unit": "g"}]
            ],
        ),
        _plan(
            date(2026, 5, 27),
            status="skipped",  # 集約対象外
            ingredients_per_dish=[
                [{"canonical": "鶏肉", "name": "鶏肉", "quantity": 200, "unit": "g"}]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    canonicals = {it.canonical for it in sl.items}
    assert canonicals == {"牛肉"}


def test_別の日の同じ食材は_合算される() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 300, "unit": "g"}]
            ],
        ),
        _plan(
            date(2026, 5, 26),
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 200, "unit": "g"}]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    items = [it for it in sl.items if it.canonical == "牛肉"]
    assert len(items) == 1
    assert _amounts(items[0]) == {"g": 500}


def test_単位混在は1行にまとめ_数量を単位ごとに並記する() -> None:
    # 仕様変更: 旧仕様は単位ごとに別行だったが, 同じ食材が複数行に出るのを防ぐため 1 行に統合する
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"canonical": "たまねぎ", "name": "玉ねぎ", "quantity": 1, "unit": "個"},
                    {"canonical": "たまねぎ", "name": "玉ねぎ", "quantity": 100, "unit": "g"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    items = [it for it in sl.items if it.canonical == "たまねぎ"]
    assert len(items) == 1
    assert _amounts(items[0]) == {"個": 1, "g": 100}


def test_大さじ_小さじ_ml_は_ml_に換算して合算() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"canonical": "大豆", "name": "醤油", "quantity": 2, "unit": "大さじ"},
                    {"canonical": "大豆", "name": "醤油", "quantity": 1.5, "unit": "小さじ"},
                    {"canonical": "大豆", "name": "醤油", "quantity": 100, "unit": "ml"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    items = [it for it in sl.items if it.canonical == "醤油"]
    assert len(items) == 1
    assert _amounts(items[0]) == {"ml": 137.5}


def test_アレルゲン用canonicalでは束ねず_食材名で集約する() -> None:
    # 醤油と豆腐はアレルゲン辞書ではどちらも canonical=大豆 だが, 買い物では別物
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"canonical": "大豆", "name": "醤油", "quantity": 1, "unit": "大さじ"},
                    {"canonical": "大豆", "name": "豆腐", "quantity": 300, "unit": "g"},
                    {"canonical": "ごま", "name": "ごま油", "quantity": 1, "unit": "小さじ"},
                    {"canonical": "乳", "name": "牛乳", "quantity": 200, "unit": "ml"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    names = {it.canonical for it in sl.items}
    assert names == {"醤油", "豆腐", "ごま油", "牛乳"}


def test_表記揺れは同じ行にまとまる() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"name": "生姜", "quantity": 10, "unit": "g"},
                    {"name": "しょうが", "quantity": 1, "unit": "片"},
                    {"name": "にんじん", "quantity": 100, "unit": "g"},
                    {"name": "ニンジン", "quantity": 50, "unit": "g"},
                    {"name": "日本酒", "quantity": 1, "unit": "大さじ"},
                    {"name": "酒", "quantity": 30, "unit": "ml"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    by_name = {it.canonical: it for it in sl.items}
    assert set(by_name) == {"しょうが", "にんじん", "酒"}
    assert _amounts(by_name["しょうが"]) == {"g": 10, "片": 1}
    assert _amounts(by_name["にんじん"]) == {"g": 150}
    assert _amounts(by_name["酒"]) == {"ml": 45}
    assert set(by_name["にんじん"].raw_names) == {"にんじん", "ニンジン"}


def test_買い分ける食材は統合しない() -> None:
    # 仕様変更: 旧仕様はアレルゲン用 canonical (鶏肉/牛肉) で束ねていたが, 部位違いは別行にする
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"canonical": "鶏肉", "name": "鶏もも肉", "quantity": 300, "unit": "g"},
                    {"canonical": "鶏肉", "name": "鶏むね肉", "quantity": 300, "unit": "g"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    assert {it.canonical for it in sl.items} == {"鶏もも肉", "鶏むね肉"}


def test_水は載せない() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"name": "水", "quantity": 400, "unit": "ml"},
                    {"name": "お湯", "quantity": 200, "unit": "ml"},
                    {"name": "ツナ缶(水煮)", "quantity": 1, "unit": "缶"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    assert [it.canonical for it in sl.items] == ["ツナ缶(水煮)"]


def test_調味料は常備品として分ける() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [
                    {"name": "豚肉", "quantity": 200, "unit": "g"},
                    {"name": "塩", "quantity": 1, "unit": "小さじ"},
                    {"name": "こしょう"},
                    {"name": "サラダ油", "quantity": 1, "unit": "大さじ"},
                    {"name": "植物油", "quantity": 10, "unit": "ml"},
                ]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    assert [it.canonical for it in sl.buy_items] == ["豚肉"]
    pantry = {it.canonical: it for it in sl.pantry_items}
    assert set(pantry) == {"塩", "こしょう", "サラダ油"}
    assert pantry["こしょう"].amounts == ()
    assert _amounts(pantry["サラダ油"]) == {"ml": 25}


def test_週外の日は除外される() -> None:
    plans = [
        _plan(
            date(2026, 5, 24),  # 1 つ前の日 (日曜)
            ingredients_per_dish=[
                [{"canonical": "豚肉", "name": "豚肉", "quantity": 200, "unit": "g"}]
            ],
        ),
        _plan(
            date(2026, 6, 1),  # 翌週
            ingredients_per_dish=[
                [{"canonical": "鶏肉", "name": "鶏肉", "quantity": 200, "unit": "g"}]
            ],
        ),
        _plan(
            date(2026, 5, 25),  # 対象
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 300, "unit": "g"}]
            ],
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    assert {it.canonical for it in sl.items} == {"牛肉"}


def test_別_family_は無視される() -> None:
    plans = [
        MealPlan(
            family_id="other",
            plan_date=date(2026, 5, 25),
            status="cooked",
            dishes=(
                MealPlanDish(
                    name="他家料理",
                    category="主菜",
                    main_ingredient="牛肉",
                    ingredients=(
                        {"canonical": "牛肉", "name": "牛肉", "quantity": 100, "unit": "g"},
                    ),
                ),
            ),
            source="single",
        ),
    ]
    sl = aggregate_week(plans, family_id="fam-1", week_start=date(2026, 5, 25))
    assert sl.items == ()


def test_manual_item_は_preserve_される() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 300, "unit": "g"}]
            ],
        ),
    ]
    manual_items = [
        ShoppingItem(canonical="トイレットペーパー", manual=True, note="切れそう"),
    ]
    sl = aggregate_week(
        plans,
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        preserve_items=manual_items,
    )
    canonicals = [it.canonical for it in sl.items]
    assert "トイレットペーパー" in canonicals
    assert "牛肉" in canonicals


def test_checked_状態は_preserve_される() -> None:
    plans = [
        _plan(
            date(2026, 5, 25),
            ingredients_per_dish=[
                [{"canonical": "牛肉", "name": "牛肉", "quantity": 300, "unit": "g"}]
            ],
        ),
    ]
    prior = [
        ShoppingItem(
            item_id="keep-this",
            canonical="牛肉",
            amounts=(ShoppingAmount(quantity=999, unit="g"),),  # 上書きされる
            checked=True,
        ),
    ]
    sl = aggregate_week(
        plans,
        family_id="fam-1",
        week_start=date(2026, 5, 25),
        preserve_items=prior,
    )
    item = next(it for it in sl.items if it.canonical == "牛肉")
    assert item.checked is True
    assert item.item_id == "keep-this"
    # quantity は再計算される
    assert _amounts(item) == {"g": 300}

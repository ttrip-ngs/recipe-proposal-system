"""食材費の概算 (recipe_system.cost) のテスト."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from recipe_system.cost import PriceBook, estimate_ingredients, estimate_meal_plan, load_price_book
from recipe_system.cost.estimator import DATA_DIR, _load_yaml, build_price_book, parse_per
from recipe_system.domain import MealPlan, MealPlanDish

PRICES: dict[str, Any] = {
    "source": "テスト",
    "area": "福岡市",
    "period": "2026-08",
    "items": {
        1203: {"name": "牛肉(輸入品)", "unit": "100g", "price_jpy": 400},
        1412: {"name": "じゃがいも", "unit": "1kg", "price_jpy": 600},
        1303: {"name": "牛乳", "unit": "1本･1,000mL", "price_jpy": 300},
        1341: {"name": "鶏卵", "unit": "1パック･10個", "price_jpy": 350},
        1473: {"name": "納豆", "unit": "1ﾊﾟｯｸ･50g\u00d73又は45g\u00d73", "price_jpy": 150},
    },
}
MAPPING: dict[str, Any] = {
    "pantry": ["醤油", "塩"],
    "entries": [
        {"code": 1203, "names": ["牛肉", "牛こま"]},
        {"code": 1412, "names": ["じゃがいも"], "pieces": {"個": 150}},
        {"code": 1303, "names": ["牛乳"]},
        {"code": 1341, "names": ["卵"]},
        {"code": 1473, "names": ["納豆"], "per": {"quantity": 3, "unit": "パック"}},
    ],
}


@pytest.fixture
def book() -> PriceBook:
    return build_price_book(PRICES, MAPPING)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("1kg", (1000.0, "g")),
        ("100g", (100.0, "g")),
        ("1袋･5kg", (5000.0, "g")),
        ("1本･1,000mL", (1000.0, "ml")),
        ("1本･450mL", (450.0, "ml")),
        ("1パック･10個", (10.0, "個")),
        ("1袋･10枚", (10.0, "枚")),
        ("1ﾊﾟｯｸ･50g\u00d73又は45g\u00d73", None),
        ("1箱･12皿分", None),
    ],
)
def test_統計の単位表記から規格量を読み取る(label: str, expected: tuple[float, str] | None) -> None:
    assert parse_per(label) == expected


def test_重量_個数_容量_卵の個数をそれぞれ換算する(book: PriceBook) -> None:
    est = estimate_ingredients(
        [
            {"name": "牛肉", "quantity": 300, "unit": "g"},  # 400/100g x 300 = 1200
            {"name": "じゃがいも", "quantity": 4, "unit": "個"},  # 600g x 0.6 = 360
            {"name": "牛乳", "quantity": 1, "unit": "カップ"},  # 200ml x 0.3 = 60
            {"name": "卵", "quantity": 2, "unit": "個"},  # 35 x 2 = 70
            {"name": "納豆", "quantity": 1, "unit": "パック"},  # 50
        ],
        book,
    )
    assert [i.status for i in est.items] == ["priced"] * 5
    assert est.total_jpy == 1200 + 360 + 60 + 70 + 50


def test_常備品は対象外_未登録と換算不能は価格不明にする(book: PriceBook) -> None:
    est = estimate_ingredients(
        [
            {"name": "醤油", "quantity": 3, "unit": "大さじ"},
            {"name": "塩", "quantity": None, "unit": None},
            {"name": "謎の食材", "quantity": 1, "unit": "個"},
            {"name": "じゃがいも", "quantity": 1, "unit": "袋"},
            {"name": "牛肉", "quantity": None, "unit": None},
        ],
        book,
    )
    assert est.total_jpy == 0
    assert est.pantry_count == 2
    assert [(i.name, i.reason) for i in est.unpriced] == [
        ("謎の食材", "価格データなし"),
        ("じゃがいも", "単位 袋 を換算不可"),
        ("牛肉", "分量不明"),
    ]


def test_括弧書き_全角_canonical_で照合できる(book: PriceBook) -> None:
    est = estimate_ingredients(
        [
            # 全角括弧・全角 g (統計表・LLM 出力に現れる表記)
            {"name": "牛こま\uff08国産\uff09", "quantity": 100, "unit": "\uff47"},
            {"name": "メークイン", "canonical": "じゃがいも", "quantity": 150, "unit": "g"},
        ],
        book,
    )
    assert [i.status for i in est.items] == ["priced", "priced"]
    assert est.total_jpy == 400 + 90


def test_重量単価の食材を容量で指定すると換算しない(book: PriceBook) -> None:
    est = estimate_ingredients([{"name": "牛肉", "quantity": 1, "unit": "大さじ"}], book)
    assert est.unpriced[0].reason == "単位 大さじ を換算不可"


def test_献立の全品目を合算する(book: PriceBook) -> None:
    plan = MealPlan(
        family_id="f",
        plan_date=date(2026, 9, 28),
        dishes=(
            MealPlanDish(
                name="a",
                category="主菜",
                main_ingredient="牛肉",
                ingredients=({"name": "牛肉", "quantity": 100, "unit": "g"},),
            ),
            MealPlanDish(
                name="b",
                category="副菜",
                main_ingredient="卵",
                ingredients=({"name": "卵", "quantity": 1, "unit": "個"},),
            ),
        ),
    )
    est = estimate_meal_plan(plan, book)
    assert est.total_jpy == 400 + 35
    assert (est + est).total_jpy == 2 * (400 + 35)


def test_対応表の食材名重複はエラー() -> None:
    mapping = {
        "entries": [
            {"code": 1203, "names": ["牛肉"]},
            {"code": 1412, "names": ["牛肉"]},
        ]
    }
    with pytest.raises(ValueError, match="重複"):
        build_price_book(PRICES, mapping)


def test_常備品と同名のエントリはエラー() -> None:
    mapping = {"pantry": ["牛肉"], "entries": [{"code": 1203, "names": ["牛肉"]}]}
    with pytest.raises(ValueError, match="重複"):
        build_price_book(PRICES, mapping)


def test_規格量を読めない銘柄で_per_未指定ならエラー() -> None:
    mapping = {"entries": [{"code": 1473, "names": ["納豆"]}]}
    with pytest.raises(ValueError, match="per"):
        build_price_book(PRICES, mapping)


def test_当月の価格表に無い銘柄は価格不明になる() -> None:
    mapping = {"entries": [{"code": 9999, "names": ["季節の果物"]}]}
    book = build_price_book(PRICES, mapping)
    est = estimate_ingredients([{"name": "季節の果物", "quantity": 1, "unit": "個"}], book)
    assert est.unpriced[0].reason == "価格データなし"


def test_同梱の価格表と対応表が読み込める() -> None:
    book = load_price_book()
    assert book.area == "福岡市"
    # 対応表の全銘柄が当月の価格表に存在する (符号の書き間違い検出)
    prices = _load_yaml(DATA_DIR / "retail_prices.yaml")
    mapping = _load_yaml(DATA_DIR / "ingredient_price_map.yaml")
    missing = [e["code"] for e in mapping["entries"] if e["code"] not in prices["items"]]
    assert missing == []

"""食材費の概算.

費用は「使用量 x 規格単価」の按分で計算する (パック単位の購入額ではない).
価格が取れない食材は推測せず合計から除外し, 呼び出し側で件数を表示する.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from recipe_system.domain import MealPlan
from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).parent / "data"

# 容量・重量系の単位 -> (基本単位, 倍率)
_MEASURE_UNITS: dict[str, tuple[str, float]] = {
    "g": ("g", 1.0),
    "グラム": ("g", 1.0),
    "kg": ("g", 1000.0),
    "ml": ("ml", 1.0),
    "cc": ("ml", 1.0),
    "l": ("ml", 1000.0),
    "大さじ": ("ml", 15.0),
    "小さじ": ("ml", 5.0),
    "カップ": ("ml", 200.0),
}

# 統計の単位表記 (例: "1kg", "100g", "1本・1,000mL", "1パック・10個") の規格量部分
_PER_RE = re.compile(r"^([\d,.]+)\s*(kg|g|ml|l|個|枚)$")
# NFKC 正規化後に適用するため全角括弧も半角として扱える
_BRACKETS_RE = re.compile(r"\(.*?\)")

CostStatus = Literal["priced", "pantry", "unpriced"]


def _norm(text: str) -> str:
    """照合用の正規化: NFKC + 括弧書き除去 + 空白除去 + 小文字化."""
    s = unicodedata.normalize("NFKC", text)
    s = _BRACKETS_RE.sub("", s)
    return "".join(s.split()).lower()


def parse_per(unit_label: str) -> tuple[float, str] | None:
    """統計の単位表記から (規格量, 基本単位) を得る. 読めない表記は None."""
    last = unicodedata.normalize("NFKC", unit_label).replace("･", "・").split("・")[-1]
    m = _PER_RE.match(last.strip().lower())
    if not m:
        return None
    qty = float(m.group(1).replace(",", ""))
    base = m.group(2)
    if base == "kg":
        return qty * 1000.0, "g"
    if base == "l":
        return qty * 1000.0, "ml"
    return qty, base


@dataclass(frozen=True)
class PriceEntry:
    code: int
    item_name: str
    unit_price_jpy: float  # 基本単位 1 あたり
    base_unit: str
    pieces: Mapping[str, float] = field(default_factory=dict)

    def to_base_quantity(self, quantity: float, unit: str | None) -> float | None:
        """(数量, 単位) を基本単位の量に換算する. 換算できない場合は None."""
        u = _norm(unit) if unit else ""
        if u == _norm(self.base_unit):
            return quantity
        if u in _MEASURE_UNITS:
            base, factor = _MEASURE_UNITS[u]
            return quantity * factor if base == self.base_unit else None
        if u in self.pieces:
            return quantity * self.pieces[u]
        return None


@dataclass(frozen=True)
class PriceBook:
    area: str
    period: str
    source: str
    entries_by_name: Mapping[str, PriceEntry]
    pantry: frozenset[str]

    def lookup(self, *names: str | None) -> PriceEntry | None:
        for n in names:
            if n and (entry := self.entries_by_name.get(_norm(n))):
                return entry
        return None

    def is_pantry(self, *names: str | None) -> bool:
        return any(n and _norm(n) in self.pantry for n in names)


@dataclass(frozen=True)
class IngredientCost:
    name: str
    quantity: float | None
    unit: str | None
    status: CostStatus
    cost_jpy: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CostEstimate:
    items: tuple[IngredientCost, ...] = ()

    @property
    def total_jpy(self) -> int:
        return round(sum(i.cost_jpy or 0.0 for i in self.items if i.status == "priced"))

    @property
    def unpriced(self) -> tuple[IngredientCost, ...]:
        return tuple(i for i in self.items if i.status == "unpriced")

    @property
    def pantry_count(self) -> int:
        return sum(1 for i in self.items if i.status == "pantry")

    def __add__(self, other: CostEstimate) -> CostEstimate:
        return CostEstimate(items=self.items + other.items)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} の形式が不正です")
    return data


def build_price_book(prices: Mapping[str, Any], mapping: Mapping[str, Any]) -> PriceBook:
    """価格表と対応表から PriceBook を組み立てる.

    対応表の同名重複・規格量の読み取り失敗は設定ミスなので例外にする.
    当月の価格表に無い銘柄 (季節品の未調査など) は警告のうえ価格不明扱いにする.
    """
    items: Mapping[int, Mapping[str, Any]] = prices["items"]
    pantry = frozenset(_norm(n) for n in mapping.get("pantry", []))
    by_name: dict[str, PriceEntry] = {}
    for raw in mapping["entries"]:
        code = int(raw["code"])
        item = items.get(code)
        if item is None:
            logger.warning("cost.price_missing", code=code, period=prices.get("period"))
            continue
        if "per" in raw:
            per: tuple[float, str] | None = (float(raw["per"]["quantity"]), raw["per"]["unit"])
        else:
            per = parse_per(str(item["unit"]))
        if per is None:
            raise ValueError(f"銘柄 {code} の単位 {item['unit']!r} を解釈できません (per を指定)")
        per_qty, base_unit = per
        entry = PriceEntry(
            code=code,
            item_name=str(item["name"]),
            unit_price_jpy=float(item["price_jpy"]) / per_qty,
            base_unit=base_unit,
            pieces={_norm(k): float(v) for k, v in (raw.get("pieces") or {}).items()},
        )
        for name in raw["names"]:
            key = _norm(name)
            if key in by_name or key in pantry:
                raise ValueError(f"食材名 {name!r} が対応表で重複しています")
            by_name[key] = entry
    return PriceBook(
        area=str(prices["area"]),
        period=str(prices["period"]),
        source=str(prices["source"]),
        entries_by_name=by_name,
        pantry=pantry,
    )


@lru_cache(maxsize=1)
def load_price_book(data_dir: Path = DATA_DIR) -> PriceBook:
    return build_price_book(
        _load_yaml(data_dir / "retail_prices.yaml"),
        _load_yaml(data_dir / "ingredient_price_map.yaml"),
    )


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _estimate_one(ing: Mapping[str, Any], book: PriceBook) -> IngredientCost:
    name = str(ing.get("name") or ing.get("canonical") or "").strip()
    canonical = ing.get("canonical")
    quantity = _to_float(ing.get("quantity"))
    unit = ing.get("unit")
    unit = str(unit) if unit is not None else None

    if book.is_pantry(name, canonical):
        return IngredientCost(name, quantity, unit, "pantry")
    entry = book.lookup(name, canonical)
    if entry is None:
        return IngredientCost(name, quantity, unit, "unpriced", reason="価格データなし")
    if quantity is None:
        return IngredientCost(name, quantity, unit, "unpriced", reason="分量不明")
    base_qty = entry.to_base_quantity(quantity, unit)
    if base_qty is None:
        return IngredientCost(name, quantity, unit, "unpriced", reason=f"単位 {unit} を換算不可")
    return IngredientCost(name, quantity, unit, "priced", cost_jpy=base_qty * entry.unit_price_jpy)


def estimate_ingredients(
    ingredients: Iterable[Mapping[str, Any]], book: PriceBook | None = None
) -> CostEstimate:
    book = book or load_price_book()
    return CostEstimate(items=tuple(_estimate_one(i, book) for i in ingredients))


def estimate_meal_plan(plan: MealPlan, book: PriceBook | None = None) -> CostEstimate:
    return estimate_ingredients((ing for dish in plan.dishes for ing in dish.ingredients), book)

"""指定週の confirmed / cooked meal_plans から ingredients を集約するサービス.

集約規則:
- 集約キーは食材 name を買い物用辞書 (shopping_dictionary.yaml) で正規化したもの.
  アレルゲン用の canonical (醤油 -> 大豆 など) は使わない
- 1 食材 1 行. 単位ごとの合算数量を amounts に並べる
  (大さじ/小さじ/カップ/cc/L は ml に, kg は g に換算してから合算)
- 水などの除外対象は載せない. 調味料などの常備品は pantry=True を付ける
- 手動追加された item (manual=True) は再生成しても保持する
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from recipe_system.domain import MealPlan, ShoppingAmount, ShoppingItem, ShoppingList
from recipe_system.observability.logging import get_logger
from recipe_system.services.shopping_dictionary import (
    ShoppingDictionary,
    ShoppingKind,
    get_shopping_dictionary,
)

logger = get_logger(__name__)

_AGGREGATABLE_STATUSES = frozenset({"confirmed", "cooked"})

# 単位 -> (換算後の単位, 倍率)
_UNIT_CONVERSIONS: dict[str, tuple[str, float]] = {
    "大さじ": ("ml", 15.0),
    "小さじ": ("ml", 5.0),
    "カップ": ("ml", 200.0),
    "ml": ("ml", 1.0),
    "cc": ("ml", 1.0),
    "l": ("ml", 1000.0),
    "g": ("g", 1.0),
    "kg": ("g", 1000.0),
}


@dataclass
class _Bucket:
    name: str
    pantry: bool
    raw_names: set[str] = field(default_factory=set)
    totals: dict[str | None, float] = field(default_factory=dict)  # unit -> 合算値

    def to_item(self) -> ShoppingItem:
        return ShoppingItem(
            canonical=self.name,
            raw_names=tuple(sorted(self.raw_names)),
            amounts=tuple(
                ShoppingAmount(quantity=round(q, 2), unit=u) for u, q in self.totals.items()
            ),
            pantry=self.pantry,
        )


def aggregate_week(
    meal_plans: Iterable[MealPlan],
    *,
    family_id: str,
    week_start: date,
    week_end: date | None = None,
    preserve_items: Iterable[ShoppingItem] = (),
    dictionary: ShoppingDictionary | None = None,
) -> ShoppingList:
    """指定週の meal_plans から ShoppingList を生成する.

    preserve_items に渡された (主に manual=True の) item は新リストにそのまま引き継ぐ.
    既存の checked 状態を保持したい場合は呼び出し側で merge して preserve_items に含める.
    """
    end = week_end or (week_start + timedelta(days=6))
    dic = dictionary or get_shopping_dictionary()

    buckets: dict[str, _Bucket] = {}
    counter = 0
    for plan in meal_plans:
        if not _is_aggregable_plan(plan, family_id=family_id, week_start=week_start, end=end):
            continue
        for dish in plan.dishes:
            for ing_raw in dish.ingredients:
                if _add_ingredient(ing_raw, buckets, dic):
                    counter += 1

    generated_items = [b.to_item() for b in buckets.values()]

    # preserve_items を後段にマージ. 既に generated 側にある canonical と被ったら
    # checked 状態だけ移植し, manual は手動として末尾に追加.
    preserved_manual: list[ShoppingItem] = []
    preserved_checked: dict[str, ShoppingItem] = {}
    for it in preserve_items:
        if it.manual:
            preserved_manual.append(it)
        else:
            preserved_checked[it.canonical] = it

    final_items: list[ShoppingItem] = []
    for gen in generated_items:
        existing = preserved_checked.get(gen.canonical)
        if existing is not None:
            final_items.append(
                gen.model_copy(
                    update={
                        "checked": existing.checked,
                        "note": existing.note,
                        "item_id": existing.item_id,
                    }
                )
            )
        else:
            final_items.append(gen)
    final_items.extend(preserved_manual)

    logger.info(
        "shopping.aggregate.done",
        family_id=family_id,
        week_start=week_start.isoformat(),
        ingredients_seen=counter,
        items_generated=len(generated_items),
        items_preserved_manual=len(preserved_manual),
    )

    return ShoppingList(
        family_id=family_id,
        week_start=week_start,
        week_end=end,
        items=tuple(final_items),
        generated_at=datetime.now(tz=UTC),
    )


def _is_aggregable_plan(plan: MealPlan, *, family_id: str, week_start: date, end: date) -> bool:
    if plan.family_id != family_id:
        return False
    if plan.status not in _AGGREGATABLE_STATUSES:
        return False
    return week_start <= plan.plan_date <= end


def _add_ingredient(ing_raw: Any, buckets: dict[str, _Bucket], dic: ShoppingDictionary) -> bool:
    name = _pick_str(ing_raw, "name") or _pick_str(ing_raw, "canonical")
    if not name:
        return False
    entry = dic.lookup(name)
    if entry.kind is ShoppingKind.EXCLUDE:
        return False
    bucket = buckets.setdefault(
        entry.key, _Bucket(name=entry.name, pantry=entry.kind is ShoppingKind.PANTRY)
    )
    bucket.raw_names.add(name)
    quantity = _pick_number(ing_raw, "quantity")
    if quantity is not None:
        unit, factor = _convert_unit(_pick_str(ing_raw, "unit"))
        bucket.totals[unit] = bucket.totals.get(unit, 0.0) + quantity * factor
    return True


def _convert_unit(unit: str | None) -> tuple[str | None, float]:
    if unit is None:
        return None, 1.0
    return _UNIT_CONVERSIONS.get(unit.lower(), (unit, 1.0))


def _pick_str(d: Any, key: str) -> str | None:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _pick_number(d: Any, key: str) -> float | None:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

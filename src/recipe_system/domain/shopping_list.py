"""買い物リスト ドメインモデル.

shopping_lists コレクションは「家族 x 週」で 1 ドキュメント.
ドキュメント ID は {family_id}_{ISO_week} で固定 (例: family-1_2026-W22).

集約規則は services/shopping_aggregator.py を参照. 1 食材 1 行で, 単位ごとの数量を
amounts に並べる. 常備品 (調味料など) は pantry=True で「買うもの」と分けて扱う.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def iso_week_label(week_start: date) -> str:
    """月曜日付から ISO 週ラベル (YYYY-Www) を生成."""
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def make_shopping_list_id(family_id: str, week_start: date) -> str:
    return f"{family_id}_{iso_week_label(week_start)}"


class ShoppingAmount(BaseModel):
    """単位ごとの合算数量."""

    quantity: float
    unit: str | None = None


class ShoppingItem(BaseModel):
    """買い物リストの 1 アイテム."""

    item_id: str = Field(default_factory=lambda: uuid4().hex)
    canonical: str = Field(..., description="買い物用の正規化名 (表示名)")
    raw_names: tuple[str, ...] = Field(default_factory=tuple, description="集約元の raw 名")
    amounts: tuple[ShoppingAmount, ...] = Field(
        default_factory=tuple, description="単位ごとの合算数量 (単位が違うものは並記)"
    )
    pantry: bool = Field(default=False, description="常備品 (調味料など). 在庫確認用に分けて表示")
    checked: bool = False
    manual: bool = False
    note: str | None = None

    @property
    def amount_text(self) -> str:
        return " + ".join(f"{a.quantity:g}{a.unit or ''}" for a in self.amounts)


class ShoppingList(BaseModel):
    """週次の買い物リスト 1 件."""

    family_id: str
    week_start: date = Field(..., description="月曜")
    week_end: date = Field(..., description="日曜")
    items: tuple[ShoppingItem, ...] = ()
    generated_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def list_id(self) -> str:
        return make_shopping_list_id(self.family_id, self.week_start)

    @property
    def iso_week(self) -> str:
        return iso_week_label(self.week_start)

    @property
    def buy_items(self) -> tuple[ShoppingItem, ...]:
        return tuple(it for it in self.items if not it.pantry)

    @property
    def pantry_items(self) -> tuple[ShoppingItem, ...]:
        return tuple(it for it in self.items if it.pantry)

    @property
    def remaining_count(self) -> int:
        """買うもの (常備品を除く) のうち未チェックの件数."""
        return sum(1 for it in self.buy_items if not it.checked)

    @property
    def total_count(self) -> int:
        return len(self.buy_items)

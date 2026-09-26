"""献立予定 ドメインモデル.

meal_plans コレクションは「家族 x 日付 x 食事枠」で一意なドキュメントを持つ.
将来 lunch / breakfast 枠が増える可能性に備えて slot フィールドを持つが,
Phase A では dinner 固定とする.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

MealPlanStatus = Literal["empty", "proposed", "confirmed", "cooked", "skipped"]
"""献立予定の状態.

- empty: その日の枠は存在するが献立未決
- proposed: LLM が提案した状態 (確定前)
- confirmed: 家族が献立を確定した状態 (買い物リスト集約対象)
- cooked: 調理済 (履歴として確定)
- skipped: その日は外食などでスキップ
"""

MealSlot = Literal["dinner"]
"""食事枠. Phase A は dinner 固定 (将来 breakfast / lunch を追加可能)."""

MealPlanSource = Literal["single", "weekly_batch", "manual"]
"""献立がどの経路で作られたか. 評価・改善時のセグメント分析に使う."""

OVERWRITABLE_STATUSES = frozenset({"empty", "proposed"})
"""再提案で上書きしてよい状態. confirmed/cooked/skipped は家族の意思決定を
保護するため上書き対象にしない."""


def can_overwrite(plan_status: MealPlanStatus) -> bool:
    """``plan_status`` の献立予定を再提案で上書きしてよいかを判定する."""
    return plan_status in OVERWRITABLE_STATUSES


class MealPlanDish(BaseModel):
    """献立内の 1 品 (主菜/副菜/汁物 のいずれか) のスナップショット.

    proposals コレクションの dishes フィールドと同じ形を取り,
    LLM 提案を採用した時点の内容を凍結する.
    """

    name: str
    category: str
    main_ingredient: str
    reason: str | None = None
    ingredients: tuple[dict[str, object], ...] = ()
    # 簡単な作り方. 手順導入前 (2026-09) に保存された献立には無い.
    steps: tuple[str, ...] = ()


class MealPlan(BaseModel):
    """家族 x 日付 x 夕食枠 の献立予定 1 件 (Phase A は dinner 固定)."""

    family_id: str
    plan_date: date = Field(..., description="JST 基準の日付 (YYYY-MM-DD)")
    slot: MealSlot = "dinner"
    status: MealPlanStatus = "empty"
    proposal_id: str | None = None
    dishes: tuple[MealPlanDish, ...] = ()
    source: MealPlanSource = "manual"
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def plan_id(self) -> str:
        """Firestore ドキュメント ID. {family_id}_{YYYY-MM-DD} で一意."""
        return make_plan_id(self.family_id, self.plan_date)


def make_plan_id(family_id: str, plan_date: date) -> str:
    return f"{family_id}_{plan_date.isoformat()}"

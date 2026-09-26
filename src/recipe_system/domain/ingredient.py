"""食材ドメインモデル."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str = Field(..., description="LLM 出力の生の食材名")
    canonical: str = Field(..., description="正規化後の代表名")
    allergen_tags: frozenset[str] = Field(
        default_factory=frozenset,
        description="所属するアレルゲングループ名の集合",
    )
    quantity: float | None = Field(None, description="数量 (任意)")
    unit: str | None = Field(None, description="単位 (任意): g, 個, ml など")

    model_config = {"frozen": True}

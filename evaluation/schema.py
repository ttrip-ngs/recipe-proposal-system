"""ゴールデンケース JSONL のスキーマ.

実装者がケース追加時に迷わないよう、構造を Pydantic で固定する.
単日 (scope: single, デフォルト) と週間 (scope: weekly) の両方を扱う.
週間ケースの runner サポートは Phase B のフォローアップで実装予定.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GoldenMember(BaseModel):
    name: str
    allergens: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)


class GoldenFamilyProfile(BaseModel):
    name: str = "テスト家"
    members: list[GoldenMember]


class GoldenHistorySummary(BaseModel):
    last_7_days: list[str] = Field(default_factory=list)
    last_14_days: list[str] = Field(default_factory=list)
    last_14_days_main_category: dict[str, int] = Field(default_factory=dict)


class GoldenInput(BaseModel):
    family_profile: GoldenFamilyProfile
    history_summary: GoldenHistorySummary = Field(default_factory=GoldenHistorySummary)
    pantry: list[str] = Field(default_factory=list)
    user_request: str | None = None
    # 週間ケースで使用
    week_start: str | None = None  # "YYYY-MM-DD" (月曜)


class ExpectedProperties(BaseModel):
    must_not_contain_allergen: list[str] = Field(default_factory=list)
    must_not_repeat_within_days: int = 7
    must_not_include_recent_dishes: list[str] = Field(default_factory=list)
    must_include_category: list[str] = Field(default_factory=list)
    soft_avoid_ingredient: list[str] = Field(default_factory=list)
    should_diversify_main_category: bool = False
    unknown_ingredient_policy: str | None = None
    # 週間ケースで使用
    must_have_days: int | None = None
    must_include_category_per_day: list[str] = Field(default_factory=list)
    must_vary_main_ingredient_within_consecutive_days: bool = False


class GoldenCase(BaseModel):
    case_id: str
    description: str | None = None
    scope: Literal["single", "weekly"] = "single"
    input: GoldenInput
    expected_properties: ExpectedProperties

"""LLM が返す JSON レスポンスの Pydantic モデル.

LLM 出力スキーマの正本. プロンプト YAML の出力フォーマット指示とここを一致させる.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from recipe_system.domain.recipe import Category


class LLMIngredient(BaseModel):
    """LLM 出力の食材 (正規化前)."""

    name: str
    quantity: float | None = None
    unit: str | None = None


class LLMDish(BaseModel):
    name: str
    category: Category
    main_ingredient: str
    reason: str = Field(..., description="なぜその料理を選んだかの簡潔な理由")
    ingredients: tuple[LLMIngredient, ...]
    # 家庭向けの簡単な作り方. 手順の文章もガードレールがアレルゲンの語で検査する.
    steps: tuple[str, ...] = Field(..., min_length=1, max_length=6)


class LLMProposal(BaseModel):
    """Sonnet 計画フェーズが返す夕食提案 (単日)."""

    dishes: tuple[LLMDish, ...] = Field(..., min_length=1, max_length=5)
    overall_comment: str | None = None


class LLMSkeletonDish(BaseModel):
    """分割経路の骨子フェーズ (Sonnet) が返す 1 品. 食材リストは含まない.

    食材リストは後段の詳細フェーズ (Haiku) が生成する. 骨子で食材まで出させると
    出力トークンが支配的になりコスト・レイテンシが下がらないため、ここでは
    「何を作るか」の決定だけに絞る.
    """

    index: int = Field(..., ge=0, description="日内での並び (0 起点). 詳細フェーズとの突合キー")
    name: str
    category: Category
    main_ingredient: str
    reason: str = Field(..., description="なぜその料理を選んだかの簡潔な理由")


class LLMWeeklySkeletonDay(BaseModel):
    """骨子フェーズが返す 1 日分."""

    day_offset: int = Field(..., ge=0, le=6, description="0 = week_start (月曜)")
    dishes: tuple[LLMSkeletonDish, ...] = Field(..., min_length=1, max_length=5)

    @model_validator(mode="after")
    def _unique_index(self) -> LLMWeeklySkeletonDay:
        """日内で index が重複しないこと.

        重複を許すと詳細フェーズとのマージ (index をキーにした辞書) が後勝ちになり、
        2 品が同じ食材リストを持つ. 安全性には影響しない (全食材が検証される) が、
        品質バグが黙って通るため、ここで弾いて再試行・単日修復に回す.
        """
        indexes = [d.index for d in self.dishes]
        if len(set(indexes)) != len(indexes):
            raise ValueError(f"日内で index が重複しています: {indexes}")
        return self


class LLMWeeklySkeleton(BaseModel):
    """骨子フェーズ (Sonnet) が返す週間 7 日分の献立骨子."""

    days: tuple[LLMWeeklySkeletonDay, ...] = Field(..., min_length=7, max_length=7)
    overall_comment: str | None = None

    @model_validator(mode="after")
    def _unique_day_offset(self) -> LLMWeeklySkeleton:
        """day_offset が 7 日分そろっていること (重複があると欠落日が生まれる)."""
        offsets = [d.day_offset for d in self.days]
        if len(set(offsets)) != len(offsets):
            raise ValueError(f"day_offset が重複しています: {sorted(offsets)}")
        return self


class LLMDishIngredients(BaseModel):
    """詳細フェーズ (Haiku) が返す 1 品分の食材リスト.

    ``name`` は骨子との突合ミスを検知するためのエコーで、マージ自体は ``index`` で行う
    (表記揺れを block に格上げしないための設計. 詳細は weekly_planner の
    ``_merge_day`` を参照).

    ``ingredients`` を ``min_length=1`` にしているのは安全側の設計で、食材リストが
    空だとガードレールの検証対象が無くなり「何も検証されずに通る」ため、
    スキーマ段階で弾いて block に落とす.
    """

    index: int = Field(..., ge=0, description="骨子の dish.index と対応する")
    name: str
    ingredients: tuple[LLMIngredient, ...] = Field(..., min_length=1)
    # 家庭向けの簡単な作り方. 手順の文章もガードレールがアレルゲンの語で検査する.
    steps: tuple[str, ...] = Field(..., min_length=1, max_length=6)


class LLMDayDetail(BaseModel):
    """詳細フェーズが返す 1 日分の食材詳細."""

    dishes: tuple[LLMDishIngredients, ...] = Field(..., min_length=1, max_length=5)


class LLMCallMeta(BaseModel):
    """提案ログに保存する呼び出しメタデータ."""

    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0

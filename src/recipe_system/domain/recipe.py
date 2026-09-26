"""レシピ ドメインモデル."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from recipe_system.domain.ingredient import Ingredient

Category = Literal["主菜", "副菜", "汁物"]


class Recipe(BaseModel):
    recipe_id: str | None = None
    name: str
    category: Category
    main_ingredient: str = Field(..., description="主食材の canonical 名")
    ingredients: tuple[Ingredient, ...]
    steps: tuple[str, ...] = ()
    tags: frozenset[str] = Field(default_factory=frozenset)
    servings: int = 4
    source: str | None = None
    created_at: datetime | None = None

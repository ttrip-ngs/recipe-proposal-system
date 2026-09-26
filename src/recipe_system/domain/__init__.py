"""ドメインモデル. Firestore 非依存の Pydantic モデル群."""

from recipe_system.domain.family import FamilyMember, FamilyProfile
from recipe_system.domain.ingredient import Ingredient
from recipe_system.domain.llm_output import (
    LLMCallMeta,
    LLMDayDetail,
    LLMDish,
    LLMDishIngredients,
    LLMIngredient,
    LLMProposal,
    LLMSkeletonDish,
    LLMWeeklySkeleton,
    LLMWeeklySkeletonDay,
)
from recipe_system.domain.meal_plan import (
    OVERWRITABLE_STATUSES,
    MealPlan,
    MealPlanDish,
    MealPlanSource,
    MealPlanStatus,
    MealSlot,
    can_overwrite,
    make_plan_id,
)
from recipe_system.domain.recipe import Recipe
from recipe_system.domain.shopping_list import (
    ShoppingAmount,
    ShoppingItem,
    ShoppingList,
    iso_week_label,
    make_shopping_list_id,
)
from recipe_system.domain.violation import Violation

__all__ = [
    "OVERWRITABLE_STATUSES",
    "FamilyMember",
    "FamilyProfile",
    "Ingredient",
    "LLMCallMeta",
    "LLMDayDetail",
    "LLMDish",
    "LLMDishIngredients",
    "LLMIngredient",
    "LLMProposal",
    "LLMSkeletonDish",
    "LLMWeeklySkeleton",
    "LLMWeeklySkeletonDay",
    "MealPlan",
    "MealPlanDish",
    "MealPlanSource",
    "MealPlanStatus",
    "MealSlot",
    "Recipe",
    "ShoppingAmount",
    "ShoppingItem",
    "ShoppingList",
    "Violation",
    "can_overwrite",
    "iso_week_label",
    "make_plan_id",
    "make_shopping_list_id",
]

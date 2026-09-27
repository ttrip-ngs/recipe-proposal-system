"""事前フィルタ (Python 決定論的処理).

三段階処理の初段で、LLM に渡す前に候補レシピを絞り込む.
- アレルゲン含有レシピの除外
- 直近 N 日に作ったレシピの除外

目的は「LLM が NG 料理を提案し続けて無駄な往復を生まない」こと. 安全担保は
ガードレール (事後検証) が本来の責務.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from recipe_system.domain import FamilyMember, Recipe
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary
from recipe_system.guardrails.validators import blocked_allergens


@dataclass(frozen=True)
class FilterStats:
    """フィルタの除外件数を可視化するためのメトリクス."""

    total: int
    after_allergen: int
    after_recency: int

    @property
    def excluded_by_allergen(self) -> int:
        return self.total - self.after_allergen

    @property
    def excluded_by_recency(self) -> int:
        return self.after_allergen - self.after_recency


def prefilter_recipes(
    recipes: Sequence[Recipe],
    family: Iterable[FamilyMember],
    recent_recipe_names: Iterable[str],
    *,
    recency_days: int = 7,  # noqa: ARG001
) -> tuple[tuple[Recipe, ...], FilterStats]:
    """候補レシピから NG を除去して返す.

    recency_days は呼び出し元で recent_recipe_names を抽出する際の判定窓であり、
    本関数は既に抽出済みの名前リストをそのまま除外する.
    """
    recipes_tuple = tuple(recipes)
    members = tuple(family)
    dictionary = default_dictionary()

    after_allergen = tuple(
        r for r in recipes_tuple if not _has_allergen_conflict(r, members, dictionary)
    )

    recent_names = frozenset(recent_recipe_names)
    after_recency = tuple(r for r in after_allergen if r.name not in recent_names)

    stats = FilterStats(
        total=len(recipes_tuple),
        after_allergen=len(after_allergen),
        after_recency=len(after_recency),
    )
    return after_recency, stats


def _has_allergen_conflict(
    recipe: Recipe, members: Sequence[FamilyMember], dictionary: NormalizerDictionary
) -> bool:
    """事後検証 (validators) と同じ判定をメンバーごとに行う.

    家族全体のアレルゲンの和集合で見ると, あるメンバーが醤油を摂取可にしていても
    除外されてしまうため, メンバー単位で ``blocked_allergens`` を使う.
    """
    return any(
        blocked_allergens(ing, member, dictionary)
        for ing in recipe.ingredients
        for member in members
    )


def recent_recipe_names(
    history: Iterable[tuple[str, datetime]],
    *,
    recency_days: int = 7,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """履歴 (recipe_name, cooked_at) から直近 N 日分の料理名集合を返す."""
    current = now or datetime.now(UTC)
    threshold = current - timedelta(days=recency_days)
    names: list[str] = []
    for name, cooked_at in history:
        cooked_utc = cooked_at if cooked_at.tzinfo else cooked_at.replace(tzinfo=UTC)
        if cooked_utc >= threshold:
            names.append(name)
    return tuple(names)

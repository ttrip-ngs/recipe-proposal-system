"""決定論的なレシピ検証.

LLM の出力をコードで機械的に検証する. アレルゲン検出は LLM に委譲しない.
"""

from __future__ import annotations

from collections.abc import Iterable

from recipe_system.domain import FamilyMember, Ingredient, Recipe, Violation
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary


def validate_recipe(
    recipe: Recipe,
    family: Iterable[FamilyMember],
    dictionary: NormalizerDictionary | None = None,
) -> tuple[Violation, ...]:
    """レシピを家族プロファイルと照合し違反を返す.

    block: アレルゲンヒット (食材リスト、または手順の文章). 絶対に通してはならない.
    warn:  嫌いな食材. 提案は通すがログに残す.

    手順は食材リストと同じ LLM 呼出で生成させ、食材リストにない食材を書かないよう
    指示しているが、指示だけでは保証できない (「溶き卵でとじる」のように手順にだけ
    アレルゲンが現れうる). そのため手順の文章もアレルゲンの語で検査する.
    """
    members = tuple(family)
    violations: list[Violation] = []
    for ingredient in recipe.ingredients:
        for member in members:
            _check_allergens(ingredient, member, violations)
            _check_dislikes(ingredient, member, violations)
    if recipe.steps:
        dic = dictionary or default_dictionary()
        for member in members:
            _check_steps(recipe.steps, member, dic, violations)
    return tuple(violations)


def has_blocking_violation(violations: Iterable[Violation]) -> bool:
    return any(v.severity == "block" for v in violations)


def _check_allergens(
    ingredient: Ingredient,
    member: FamilyMember,
    sink: list[Violation],
) -> None:
    overlap = ingredient.allergen_tags & member.allergens
    if not overlap:
        return
    sink.append(
        Violation(
            severity="block",
            member=member.name,
            ingredient=ingredient.name,
            canonical=ingredient.canonical,
            reason=f"アレルゲン: {', '.join(sorted(overlap))}",
        )
    )


def _check_steps(
    steps: Iterable[str],
    member: FamilyMember,
    dictionary: NormalizerDictionary,
    sink: list[Violation],
) -> None:
    """手順の文章にメンバーのアレルゲンの語 (canonical / エイリアス) が現れたら block.

    日本語は分かち書きされないため部分一致で検査する. 誤検出しやすい短い語は
    辞書側の ``text_match_exclude`` で外す (コードに除外ロジックを持たない).
    """
    if not member.allergens:
        return
    terms = dictionary.text_terms_for(member.allergens)
    for step in steps:
        lowered = step.lower()
        for term, canonical in sorted(terms.items()):
            if term not in lowered:
                continue
            sink.append(
                Violation(
                    severity="block",
                    member=member.name,
                    ingredient=term,
                    canonical=canonical,
                    reason=f"手順にアレルゲン: {canonical} ({step})",
                )
            )


def _check_dislikes(
    ingredient: Ingredient,
    member: FamilyMember,
    sink: list[Violation],
) -> None:
    if ingredient.canonical not in member.dislikes:
        return
    sink.append(
        Violation(
            severity="warn",
            member=member.name,
            ingredient=ingredient.name,
            canonical=ingredient.canonical,
            reason="苦手食材",
        )
    )

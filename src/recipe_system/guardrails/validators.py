"""決定論的なレシピ検証.

LLM の出力をコードで機械的に検証する. アレルゲン検出は LLM に委譲しない.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from recipe_system.domain import FamilyMember, Ingredient, Recipe, Violation
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary
from recipe_system.text_normalize import bracket_contents


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

    辞書に無い食材 (allergen_tags が空) は名前も同じ語で検査する. 「溶き卵」「鶏ひき肉」
    のような複合語は辞書の完全一致では引けず, タグ無しで素通りするため. 辞書にある食材も
    括弧書きの中身 (「牛乳 (または豆乳)」の豆乳) は検査する.
    """
    members = tuple(family)
    dic = dictionary or default_dictionary()
    violations: list[Violation] = []
    for ingredient in recipe.ingredients:
        for member in members:
            _check_allergens(ingredient, member, violations)
            _check_dislikes(ingredient, member, violations)
    # 括弧の外が辞書にある食材 (「牛乳 (または豆乳)」) はタグが付くため, 括弧内も別に見る
    name_texts = [i.name for i in recipe.ingredients if not i.allergen_tags]
    name_texts += [
        c for i in recipe.ingredients if i.allergen_tags for c in bracket_contents(i.name)
    ]
    for member in members:
        _check_text(name_texts, "食材名", member, dic, violations)
        _check_text(recipe.steps, "手順", member, dic, violations)
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


def _check_text(
    texts: Iterable[str],
    label: str,
    member: FamilyMember,
    dictionary: NormalizerDictionary,
    sink: list[Violation],
) -> None:
    """文章 (手順・辞書に無い食材名) にメンバーのアレルゲンの語 (canonical / エイリアス)
    が現れたら block.

    日本語は分かち書きされないため部分一致で検査する. 誤検出しやすい短い語は
    辞書側の ``text_match_exclude`` で外す (コードに除外ロジックを持たない).
    全角英数・半角カナは NFKC で揃えてから照合する.
    """
    if not member.allergens:
        return
    terms = dictionary.text_terms_for(member.allergens)
    for text in texts:
        lowered = unicodedata.normalize("NFKC", text).lower()
        # 「鶏もも肉」に 鶏 / 鶏もも が当たるように, 同じ食材の語が複数当たっても 1 件にする
        reported: set[str] = set()
        for term, canonical in sorted(terms.items()):
            if term not in lowered or canonical in reported:
                continue
            reported.add(canonical)
            sink.append(
                Violation(
                    severity="block",
                    member=member.name,
                    ingredient=term,
                    canonical=canonical,
                    reason=f"{label}にアレルゲン: {canonical} ({text})",
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

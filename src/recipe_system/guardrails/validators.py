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
            _check_allergens(ingredient, member, dic, violations)
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


def blocked_allergens(
    ingredient: Ingredient,
    member: FamilyMember,
    dictionary: NormalizerDictionary,
) -> frozenset[str]:
    """食材がメンバーにとって除去対象になるアレルギー指定 (空なら摂取可).

    事前フィルタと事後検証が同じ判定を使うよう, 判定はここに 1 つだけ置く.
    通常は除去不要な食品 (醤油など) は, そのアレルギー指定で ``allow`` が選ばれている
    ときだけ除外する. 未選択は除去 (ADR 0007). 醤油のように複数のグループに属する
    食品は, 該当する全てのアレルギー指定で allow のときだけ摂取可になる.
    """
    return _blocked(ingredient.canonical, ingredient.allergen_tags, member, dictionary)


def allowed_items(member: FamilyMember, dictionary: NormalizerDictionary) -> frozenset[str]:
    """メンバーが摂取してよい「通常は除去不要な食品」の canonical."""
    candidates = {i for a in member.allergens for i in dictionary.tolerable_items(a)}
    allowed: set[str] = set()
    for item in candidates:
        canonical, tags = dictionary.normalize(item)
        if not _blocked(canonical, tags, member, dictionary):
            allowed.add(canonical)
    return frozenset(allowed)


def undecided_items(
    member: FamilyMember, dictionary: NormalizerDictionary
) -> tuple[tuple[str, str], ...]:
    """可/不可が未選択の (アレルギー指定, 食品). 判定では除去扱いだが, 画面で設定を促す."""
    return tuple(
        (a, item)
        for a in sorted(member.allergens)
        for item in sorted(dictionary.tolerable_items(a))
        if item not in member.item_policies.get(a, {})
    )


def _blocked(
    canonical: str,
    tags: frozenset[str],
    member: FamilyMember,
    dictionary: NormalizerDictionary,
) -> frozenset[str]:
    return frozenset(
        a for a in tags & member.allergens if not _is_allowed(a, canonical, member, dictionary)
    )


def _is_allowed(
    allergen: str, canonical: str, member: FamilyMember, dictionary: NormalizerDictionary
) -> bool:
    if canonical not in dictionary.tolerable_items(allergen):
        return False
    return member.item_policies.get(allergen, {}).get(canonical) == "allow"


def _check_allergens(
    ingredient: Ingredient,
    member: FamilyMember,
    dictionary: NormalizerDictionary,
    sink: list[Violation],
) -> None:
    blocked = blocked_allergens(ingredient, member, dictionary)
    if not blocked:
        return
    sink.append(
        Violation(
            severity="block",
            member=member.name,
            ingredient=ingredient.name,
            canonical=ingredient.canonical,
            reason=f"アレルゲン: {', '.join(sorted(blocked))}",
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
    # 摂取可にした食品は名前にアレルゲンの語を含む (ごま油 -> ごま, 大豆油 -> 大豆).
    # 語の出現位置が摂取可の食品の表記の内側に収まるときだけ無視する. 本文を書き換えて
    # 伏せると「煮込みそば」の「みそ」(味噌) を消して「そば」まで壊すため, 位置で判定する
    allowed = allowed_items(member, dictionary)
    terms = {
        t: c for t, c in dictionary.text_terms_for(member.allergens).items() if c not in allowed
    }
    allowed_terms = [t for t, c in dictionary.alias_to_canonical.items() if c in allowed]
    for text in texts:
        lowered = unicodedata.normalize("NFKC", text).lower()
        allowed_spans = [span for t in allowed_terms for span in _spans(lowered, t)]
        # 「鶏もも肉」に 鶏 / 鶏もも が当たるように, 同じ食材の語が複数当たっても 1 件にする
        reported: set[str] = set()
        for term, canonical in sorted(terms.items()):
            if canonical in reported or not any(
                not _inside(span, allowed_spans) for span in _spans(lowered, term)
            ):
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


def _spans(text: str, term: str) -> list[tuple[int, int]]:
    """text 中の term の出現区間 (重なりも含む)."""
    spans = []
    start = text.find(term)
    while start != -1:
        spans.append((start, start + len(term)))
        start = text.find(term, start + 1)
    return spans


def _inside(span: tuple[int, int], containers: list[tuple[int, int]]) -> bool:
    return any(c_start <= span[0] and span[1] <= c_end for c_start, c_end in containers)

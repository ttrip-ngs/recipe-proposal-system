"""単日提案 (suggest_dinner) と週間提案 (weekly_planner) が共有するヘルパー.

両ユースケースとも「LLM 出力の正規化 -> ガードレール検証 -> Firestore レコード整形」
という同一の変換ロジックを持つため、ここに集約する. 差異 (バリデーション対象の
Pydantic モデル、ログイベント名) は呼び出し側が引数で渡す.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from recipe_system.domain import FamilyProfile, Ingredient, LLMDish, Recipe, Violation
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary
from recipe_system.guardrails.validators import allowed_items, validate_recipe
from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)


class LLMResponseParseError(ValueError):
    """LLM 出力の JSON / スキーマ検証失敗."""


# ```json ... ``` 形式のコードフェンスを剥がすためのパターン.
# Claude は system プロンプトで JSON スキーマのみを提示しても応答をフェンスで
# 包むことが多い (実 API 検証で確認済み: Sonnet 5 / Haiku 4.5 とも再現).
# Gemini は response_mime_type=application/json で回避しているが、Anthropic 系には
# 同等の指定がないため、プロバイダ非依存の後処理としてここで吸収する.
_CODE_FENCE_RE = re.compile(r"\A\s*```(?:json)?\s*\n(.*?)\n?\s*```\s*\Z", re.DOTALL)


def _strip_code_fence(raw: str) -> str:
    """markdown コードフェンスで包まれていれば中身だけを返す (無ければ原文のまま)."""
    matched = _CODE_FENCE_RE.match(raw)
    return matched.group(1) if matched else raw


def parse_llm_json[T: BaseModel](raw: str, *, model: type[T], schema_error_label: str) -> T:
    """LLM の生テキストを JSON デコードし、指定モデルでスキーマ検証する.

    markdown コードフェンスで包まれている場合は剥がしてからデコードする.
    失敗時は ``LLMResponseParseError`` に統一する (呼び出し側は 1 回だけ再要求できる).
    """
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(f"JSON デコード失敗: {e}") from e
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise LLMResponseParseError(f"{schema_error_label}: {e}") from e


async def attempt_with_parse_retry[R](
    single_attempt: Callable[..., Awaitable[R]],
    *,
    log_name: str,
    **kwargs: Any,
) -> R:
    """``single_attempt(**kwargs)`` を実行し、JSON パース失敗時のみ 1 回だけ再要求する.

    ガードレール違反によるリトライ (呼出元が別途行う) とは独立した仕組み. 実 LLM で
    まれに不正な JSON が返るケースを吸収する. 再試行後も失敗すれば
    ``LLMResponseParseError`` を伝播し、呼出元 (web 層) がフェイルオープンせず
    エラー扱いする.
    """
    try:
        return await single_attempt(**kwargs)
    except LLMResponseParseError:
        prompt_version = kwargs["system_prompt"].version
        logger.warning(f"{log_name}.parse_error.retry", prompt_version=prompt_version)
        return await single_attempt(**kwargs)


def normalize_dish[D](
    dish: LLMDish,
    dictionary: NormalizerDictionary,
    *,
    normalized_dish_cls: Callable[..., D],
) -> D:
    """LLM 出力の Dish を正規化する.

    ``NormalizedDish`` は suggest_dinner.py が正本の frozen dataclass のため、
    ここではコンストラクタを引数で受け取ってインスタンス化する (循環 import 回避).
    """
    normalized_ingredients: list[Ingredient] = []
    for ing in dish.ingredients:
        canonical, tags = dictionary.normalize(ing.name)
        normalized_ingredients.append(
            Ingredient(
                name=ing.name,
                canonical=canonical,
                allergen_tags=tags,
                quantity=ing.quantity,
                unit=ing.unit,
            )
        )
    return normalized_dish_cls(
        name=dish.name,
        category=dish.category,
        main_ingredient=dish.main_ingredient,
        reason=dish.reason,
        ingredients=tuple(normalized_ingredients),
        steps=tuple(dish.steps),
    )


def collect_violations(
    dishes: Sequence[Any],
    family: FamilyProfile,
    dictionary: NormalizerDictionary | None = None,
) -> tuple[Violation, ...]:
    """正規化済み Dish (name/category/main_ingredient/ingredients/steps を持つもの) を検証する.

    手順 (steps) の文章も ``validate_recipe`` がアレルゲンの語で検査する.
    """
    all_violations: list[Violation] = []
    for dish in dishes:
        pseudo_recipe = Recipe(
            name=dish.name,
            category=dish.category,
            main_ingredient=dish.main_ingredient,
            ingredients=dish.ingredients,
            steps=dish.steps,
        )
        all_violations.extend(validate_recipe(pseudo_recipe, family.members, dictionary))
    return tuple(all_violations)


def build_allergen_summary(
    family: FamilyProfile, dictionary: NormalizerDictionary | None = None
) -> list[dict[str, Any]]:
    """プロンプトに渡すメンバーごとのアレルゲン一覧.

    通常は除去不要な食品 (醤油など) の可/不可も文字列に含める (ADR 0007). 未選択は
    ガードレールで除去扱いになるため, LLM にも「除去」と伝えて無駄な再依頼を防ぐ.
    例: 「大豆 (醤油・大豆油は使用可、味噌も除去)」「小麦 (醤油・酢・麦茶・味噌も除去)」
    """
    dic = dictionary or default_dictionary()
    summary = []
    for m in family.members:
        if not m.allergens:
            continue
        # 使用可はガードレールと同じ判定 (該当する全アレルゲンで可) に揃える. 醤油を大豆で可・
        # 小麦で未選択にしたメンバーに「醤油は使用可」と「醤油も除去」を同時に伝えないため
        allowed = allowed_items(m, dic)
        described = [_describe_allergen(a, allowed, dic) for a in sorted(m.allergens)]
        summary.append({"member": m.name, "allergens": described})
    return summary


def _describe_allergen(allergen: str, allowed: frozenset[str], dic: NormalizerDictionary) -> str:
    items = sorted(dic.tolerable_items(allergen))
    if not items:
        return allergen
    usable = [i for i in items if i in allowed]
    removed = [i for i in items if i not in allowed]
    parts = []
    if usable:
        parts.append(f"{'・'.join(usable)}は使用可")
    if removed:
        parts.append(f"{'・'.join(removed)}も除去")
    return f"{allergen} ({'、'.join(parts)})"


def build_dislike_summary(family: FamilyProfile) -> list[dict[str, Any]]:
    return [
        {"member": m.name, "dislikes": sorted(m.dislikes)} for m in family.members if m.dislikes
    ]


def family_to_mapping(family: FamilyProfile) -> dict[str, Any]:
    return {
        "family_id": family.family_id,
        "name": family.name,
        "members": [
            {
                "name": m.name,
                "role": m.role,
                "allergens": sorted(m.allergens),
                "dislikes": sorted(m.dislikes),
                "likes": sorted(m.likes),
            }
            for m in family.members
        ],
    }


def recipe_to_mapping(recipe: Recipe) -> dict[str, Any]:
    return {
        "name": recipe.name,
        "category": recipe.category,
        "main_ingredient": recipe.main_ingredient,
        "tags": sorted(recipe.tags),
    }


def dish_to_record(dish: Any) -> dict[str, Any]:
    """正規化済み Dish (NormalizedDish 相当) を Firestore 保存用 dict に変換する."""
    return {
        "name": dish.name,
        "category": dish.category,
        "main_ingredient": dish.main_ingredient,
        "reason": dish.reason,
        "ingredients": [
            {
                "name": ing.name,
                "canonical": ing.canonical,
                "allergen_tags": sorted(ing.allergen_tags),
                "quantity": ing.quantity,
                "unit": ing.unit,
            }
            for ing in dish.ingredients
        ],
        "steps": list(dish.steps),
    }

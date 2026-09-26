"""夕食提案ユースケースの三段階処理オーケストレーション.

  [事前フィルタ (Python)] -> [LLM 提案 (Sonnet)] -> [事後検証 (コード)]

ガードレール違反時のリトライは最大 1 回. 2 回目以降はフェイルオープンせず失敗として返す.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from recipe_system.domain import (
    FamilyProfile,
    Ingredient,
    LLMCallMeta,
    LLMProposal,
    Recipe,
    Violation,
)
from recipe_system.domain.recipe import Category
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary
from recipe_system.guardrails.validators import has_blocking_violation
from recipe_system.llm.client import LLMClient
from recipe_system.observability.logging import get_logger
from recipe_system.services.filters import prefilter_recipes, recent_recipe_names
from recipe_system.services.prompts import Prompt, get_prompt
from recipe_system.services.suggestion_common import (
    LLMResponseParseError,
    attempt_with_parse_retry,
    build_allergen_summary,
    build_dislike_summary,
    collect_violations,
    dish_to_record,
    family_to_mapping,
    normalize_dish,
    parse_llm_json,
    recipe_to_mapping,
)

logger = get_logger(__name__)

SYSTEM_PROMPT_NAME = "suggest_dinner_system"
USER_PROMPT_NAME = "suggest_dinner_user"

# 3 品 (各料理の食材リスト付き) を出すのに、Sonnet 5 の実測で 1100-2000 トークンと
# 幅がある。LLMClient の既定 2000 では出力が途中で切れて JSON パースに失敗する
# ケースが実際に発生したため、明示的に余裕を持たせる。
# 出力トークンは生成された分だけ課金されるので、上限を上げてもコストは増えない。
_SINGLE_DAY_MAX_TOKENS = 4000

# 互換用 re-export. weekly_planner.py と evaluation/runner.py がここから import する.
__all__ = [
    "DinnerProposal",
    "LLMResponseParseError",
    "NormalizedDish",
    "SuggestContext",
    "suggest_dinner",
]


@dataclass(frozen=True)
class SuggestContext:
    """提案生成に必要な入力一式."""

    family: FamilyProfile
    all_recipes: Sequence[Recipe]
    recent_history: Sequence[tuple[str, datetime]]
    pantry: Sequence[str] = ()
    user_request: str | None = None
    recency_days: int = 7


@dataclass(frozen=True)
class NormalizedDish:
    """LLM 出力を正規化したあとの Dish."""

    name: str
    category: Category
    main_ingredient: str
    reason: str
    ingredients: tuple[Ingredient, ...]
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class DinnerProposal:
    """三段階処理の最終成果物. 提案ログ (Firestore) にもこの内容を保存する."""

    dishes: tuple[NormalizedDish, ...]
    violations: tuple[Violation, ...]
    retry_count: int
    llm_meta: LLMCallMeta
    prompt_version: str
    overall_comment: str | None
    succeeded: bool

    @property
    def has_block(self) -> bool:
        return has_blocking_violation(self.violations)

    def to_firestore_record(self) -> dict[str, Any]:
        """Firestore 書き込み用の dict に変換する (repository 層は services に依存しない)."""
        return {
            "status": "ready",
            "prompt_version": self.prompt_version,
            "model": self.llm_meta.model,
            "input_tokens": self.llm_meta.input_tokens,
            "output_tokens": self.llm_meta.output_tokens,
            "cache_read_tokens": self.llm_meta.cache_read_tokens,
            "cache_write_tokens": self.llm_meta.cache_write_tokens,
            "latency_ms": self.llm_meta.latency_ms,
            "dishes": [dish_to_record(d) for d in self.dishes],
            "violations": [v.model_dump() for v in self.violations],
            "retry_count": self.retry_count,
            "overall_comment": self.overall_comment,
            "succeeded": self.succeeded,
        }


@dataclass
class _AttemptResult:
    proposal: LLMProposal
    dishes: tuple[NormalizedDish, ...]
    violations: tuple[Violation, ...]
    meta: LLMCallMeta


async def suggest_dinner(
    context: SuggestContext,
    *,
    llm: LLMClient,
    dictionary: NormalizerDictionary | None = None,
) -> DinnerProposal:
    """夕食提案の三段階処理を実行する.

    リトライは最大 1 回. 2 回目もブロックがあれば succeeded=False で返す.
    提案ログの永続化は呼び出し側 (web 層) の責務とする.
    """
    dic = dictionary or default_dictionary()
    system_prompt = get_prompt(SYSTEM_PROMPT_NAME)
    user_prompt = get_prompt(USER_PROMPT_NAME)

    recent_names = recent_recipe_names(context.recent_history, recency_days=context.recency_days)
    candidates, stats = prefilter_recipes(
        context.all_recipes,
        context.family.members,
        recent_names,
    )
    logger.info(
        "prefilter.done",
        total=stats.total,
        excluded_by_allergen=stats.excluded_by_allergen,
        excluded_by_recency=stats.excluded_by_recency,
        remaining=stats.after_recency,
    )

    allergen_summary = build_allergen_summary(context.family)
    dislike_summary = build_dislike_summary(context.family)

    base_variables: dict[str, Any] = {
        "family_profile": family_to_mapping(context.family),
        "allergen_summary": allergen_summary,
        "dislike_summary": dislike_summary,
        "recent_recipe_names": list(recent_names),
        "previous_violations": [],
    }
    user_variables = {
        "candidate_recipes": [recipe_to_mapping(r) for r in candidates],
        "pantry": list(context.pantry),
        "user_request": context.user_request,
    }

    attempt = await attempt_with_parse_retry(
        _single_attempt,
        log_name="llm",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        base_variables=base_variables,
        user_variables=user_variables,
        family=context.family,
        dictionary=dic,
        llm=llm,
    )

    retry_count = 0
    if has_blocking_violation(attempt.violations):
        logger.warning(
            "guard.block_on_first_attempt",
            prompt_version=attempt.meta.prompt_version,
            violations=[v.model_dump() for v in attempt.violations],
        )
        retry_variables = {
            **base_variables,
            "previous_violations": [_violation_to_mapping(v) for v in attempt.violations],
        }
        attempt = await attempt_with_parse_retry(
            _single_attempt,
            log_name="llm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            base_variables=retry_variables,
            user_variables=user_variables,
            family=context.family,
            dictionary=dic,
            llm=llm,
        )
        retry_count = 1

    succeeded = not has_blocking_violation(attempt.violations)
    if not succeeded:
        logger.error(
            "guard.block_after_retry",
            prompt_version=attempt.meta.prompt_version,
            violations=[v.model_dump() for v in attempt.violations],
        )

    return DinnerProposal(
        dishes=attempt.dishes,
        violations=attempt.violations,
        retry_count=retry_count,
        llm_meta=attempt.meta,
        prompt_version=attempt.meta.prompt_version,
        overall_comment=attempt.proposal.overall_comment,
        succeeded=succeeded,
    )


async def _single_attempt(
    *,
    system_prompt: Prompt,
    user_prompt: Prompt,
    base_variables: dict[str, Any],
    user_variables: dict[str, Any],
    family: FamilyProfile,
    dictionary: NormalizerDictionary,
    llm: LLMClient,
) -> _AttemptResult:
    system_text = system_prompt.render(**base_variables)
    user_text = user_prompt.render(**user_variables)

    response = await llm.generate(
        system=system_text,
        user=user_text,
        prompt_version=system_prompt.version,
        max_tokens=_SINGLE_DAY_MAX_TOKENS,
        output_schema=LLMProposal,
    )

    try:
        parsed = parse_llm_json(
            response.raw_text, model=LLMProposal, schema_error_label="スキーマ検証失敗"
        )
    except LLMResponseParseError:
        logger.exception("llm.parse_error", raw_preview=response.raw_text[:500])
        raise

    normalized_dishes = tuple(
        normalize_dish(d, dictionary, normalized_dish_cls=NormalizedDish) for d in parsed.dishes
    )
    violations = collect_violations(normalized_dishes, family, dictionary)

    meta = LLMCallMeta(
        model=response.model,
        prompt_version=system_prompt.version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        cache_write_tokens=response.cache_write_tokens,
        latency_ms=response.latency_ms,
    )
    return _AttemptResult(
        proposal=parsed,
        dishes=normalized_dishes,
        violations=violations,
        meta=meta,
    )


def _violation_to_mapping(v: Violation) -> dict[str, Any]:
    return {
        "member": v.member,
        "ingredient": v.ingredient,
        "canonical": v.canonical,
        "reason": v.reason,
    }

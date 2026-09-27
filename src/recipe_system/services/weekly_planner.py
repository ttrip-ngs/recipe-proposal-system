"""週間 (7 日分) 夕食提案ユースケースのオーケストレーション.

  [事前フィルタ] -> [骨子 1 回 (llm)] -> [日別の食材と手順 7 並列 (detail_llm)]
  -> [マージ] -> [事後検証 (コード, 各日)]

block 違反が出た日のみ、単日提案 (suggest_dinner) で最大 1 回
作り直す. リトライ後も残ればその日だけ status=empty で返し, 他の日は提案を確定する
(週全体フェイルにはしない). 違反のなかった日は 1 回目の結果をそのまま保持する.

設計上の要点:

- 骨子では食材リストを出させない. 出力トークンがコスト・レイテンシの支配項のため、
  21 品分の食材と手順を 1 回で書かせると日別の並列化の意味がなくなる
- 骨子と詳細のマージは ``index`` で行い、料理名は照合用のエコーとしてのみ検査する
  (名前の表記揺れで block に落とすと、安全性に無関係な理由で高価な単日修復に
  エスカレーションしてしまうため. 食材がどの料理に紐付いたかに関わらず、
  ``collect_violations`` は日内の全食材を検証するので安全性は変わらない)
- 詳細が全日失敗した場合のみ例外を投げる. 確定できる日が 1 つも無い状態で
  7 日分の単日修復に流れると、コストもレイテンシも大きく悪化するため
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from recipe_system.domain import (
    FamilyProfile,
    LLMCallMeta,
    LLMDayDetail,
    LLMDish,
    LLMWeeklySkeleton,
    LLMWeeklySkeletonDay,
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
from recipe_system.services.suggest_dinner import (
    LLMResponseParseError,
    NormalizedDish,
    SuggestContext,
    suggest_dinner,
)
from recipe_system.services.suggestion_common import (
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
from recipe_system.text_normalize import fold_key

logger = get_logger(__name__)

SKELETON_SYSTEM_PROMPT_NAME = "plan_weekly_skeleton_system"
SKELETON_USER_PROMPT_NAME = "plan_weekly_skeleton_user"
DETAIL_SYSTEM_PROMPT_NAME = "detail_day_ingredients_system"
DETAIL_USER_PROMPT_NAME = "detail_day_ingredients_user"
DAYS_IN_WEEK = 7

# 骨子は 21 品の name/category/main_ingredient/reason のみで食材リストを含まない.
# max_tokens には thinking も含まれる. Opus 5.5 (effort=medium) の実測は thinking 込みで
# 3,100-3,500 トークン. 途中で切れると JSON が閉じずパース失敗するため、生成分しか
# 課金されないことを踏まえて十分大きく 12000 とする.
_SKELETON_MAX_TOKENS = 12000

# 詳細は 1 日 3 品分の食材リストと手順. Opus 5.5 (effort=low) の実測は 1 日あたり
# 約 1,050 トークン (thinking なし) のため、4000 で十分な余裕がある.
_DETAIL_MAX_TOKENS = 4000


class WeeklyDetailAllFailedError(RuntimeError):
    """分割経路で 7 日すべての食材詳細生成が失敗した場合に投げる.

    1 日でも確定できるなら、失敗日だけを単日修復に回して他の日は確定させる方針だが、
    全日失敗の場合は確定できる日が 1 つも無い. そのまま 7 日分の単日修復
    (Sonnet フル生成) に流すとレガシー経路よりコストもレイテンシも悪化するため、
    ここで打ち切って呼出元にエラーとして返す. フェイルオープンはしない.

    既知の挙動: 骨子の後に月次予算上限を跨ぐと詳細 7 件が全て BudgetExceededError で
    失敗し、この例外に変換される. その結果 proposal の status は budget_exceeded では
    なく error になり、UI も予算案内ではなく一般エラー文言になる. 月末境界でしか
    起きず、予算ガード自体は正しく機能している (課金は止まる) ため、原因別の
    再送出は実装していない.
    """


@dataclass(frozen=True)
class WeeklySuggestContext:
    """週間提案生成に必要な入力一式."""

    family: FamilyProfile
    all_recipes: Sequence[Recipe]
    recent_history: Sequence[tuple[str, datetime]]
    week_start: date
    pantry: Sequence[str] = ()
    user_request: str | None = None
    recency_days: int = 14


@dataclass(frozen=True)
class DailyResult:
    """週間提案内の 1 日分の結果."""

    plan_date: date
    dishes: tuple[NormalizedDish, ...]
    violations: tuple[Violation, ...]
    succeeded: bool


@dataclass(frozen=True)
class WeeklyDinnerProposal:
    """週間提案の最終成果物."""

    week_start: date
    days: tuple[DailyResult, ...]
    retry_count: int
    llm_meta: LLMCallMeta
    prompt_version: str
    overall_comment: str | None

    @property
    def succeeded_days(self) -> int:
        return sum(1 for d in self.days if d.succeeded)

    @property
    def all_succeeded(self) -> bool:
        return all(d.succeeded for d in self.days)

    def to_firestore_record(self) -> dict[str, Any]:
        """proposals コレクション書き込み用 dict."""
        return {
            "status": "ready",
            "scope": "weekly",
            "week_start": self.week_start.isoformat(),
            "prompt_version": self.prompt_version,
            "model": self.llm_meta.model,
            "input_tokens": self.llm_meta.input_tokens,
            "output_tokens": self.llm_meta.output_tokens,
            "cache_read_tokens": self.llm_meta.cache_read_tokens,
            "cache_write_tokens": self.llm_meta.cache_write_tokens,
            "latency_ms": self.llm_meta.latency_ms,
            "days": [_day_to_record(d) for d in self.days],
            "retry_count": self.retry_count,
            "overall_comment": self.overall_comment,
            "succeeded": self.all_succeeded,
        }


def _day_to_record(day: DailyResult) -> dict[str, Any]:
    return {
        "plan_date": day.plan_date.isoformat(),
        "succeeded": day.succeeded,
        "dishes": [dish_to_record(d) for d in day.dishes],
        "violations": [v.model_dump() for v in day.violations],
    }


async def plan_week(
    context: WeeklySuggestContext,
    *,
    llm: LLMClient,
    detail_llm: LLMClient,
    dictionary: NormalizerDictionary | None = None,
) -> WeeklyDinnerProposal:
    """週間提案の三段階処理を実行する.

    ``llm`` で骨子 (7 日 x 3 品の料理選定) を作り、``detail_llm`` で日ごとの食材と
    手順を並列生成する. 各日にガードレールを適用し, block 違反のある日のみ
    suggest_dinner による単日提案で
    作り直す (最大 1 回). 違反のなかった日は 1 回目の結果をそのまま確定し, リトライ後も
    違反が残ればその日だけ succeeded=False で返す.
    """
    dic = dictionary or default_dictionary()

    recent_names = recent_recipe_names(context.recent_history, recency_days=context.recency_days)
    candidates, stats = prefilter_recipes(
        context.all_recipes,
        context.family.members,
        recent_names,
    )
    logger.info(
        "weekly.prefilter.done",
        total=stats.total,
        excluded_by_allergen=stats.excluded_by_allergen,
        excluded_by_recency=stats.excluded_by_recency,
        remaining=stats.after_recency,
        week_start=context.week_start.isoformat(),
    )

    candidate_mappings = [recipe_to_mapping(r) for r in candidates]
    attempt, prompt_version, detail_metas = await _generate_split(
        context,
        candidate_mappings=candidate_mappings,
        recent_names=recent_names,
        dictionary=dic,
        llm=llm,
        detail_llm=detail_llm,
    )

    weekly_meta = attempt[0].meta if attempt else _empty_meta(prompt_version)
    overall_comment = attempt[0].overall_comment if attempt else None

    retry_count = 0
    repair_metas: list[LLMCallMeta] = []
    blocked_days = [d for d in attempt if has_blocking_violation(d.violations)]
    if blocked_days:
        logger.warning(
            "weekly.guard.block_on_first_attempt",
            blocked_count=len(blocked_days),
            blocked_dates=[d.plan_date.isoformat() for d in blocked_days],
        )
        # return_exceptions=True は必須. 修復 1 日の失敗 (パース失敗の伝播や
        # 予算超過) で gather が例外を投げると、週間生成が成功していた他の日まで
        # 巻き込んで週全体がエラーになり、「その日だけ status=empty で返し他の日は
        # 確定する」という本ユースケースの前提が崩れる.
        repaired_or_errors = await asyncio.gather(
            *(
                _repair_day(day, attempt, context=context, dictionary=dic, llm=llm)
                for day in blocked_days
            ),
            return_exceptions=True,
        )
        repaired_days = [
            _repair_failure_to_day(day, result, prompt_version=prompt_version)
            if isinstance(result, BaseException)
            else result
            for day, result in zip(blocked_days, repaired_or_errors, strict=True)
        ]
        repaired_by_date = {d.plan_date: d for d in repaired_days}
        attempt = [repaired_by_date.get(d.plan_date, d) for d in attempt]
        repair_metas = [d.meta for d in repaired_days]
        retry_count = 1

    succeeded_days = [d for d in attempt if not has_blocking_violation(d.violations)]
    if len(succeeded_days) < DAYS_IN_WEEK:
        logger.warning(
            "weekly.guard.partial_failure",
            succeeded=len(succeeded_days),
            total=DAYS_IN_WEEK,
        )

    final_days = tuple(
        DailyResult(
            plan_date=d.plan_date,
            dishes=d.dishes if not has_blocking_violation(d.violations) else (),
            violations=d.violations,
            succeeded=not has_blocking_violation(d.violations),
        )
        for d in attempt
    )

    return WeeklyDinnerProposal(
        week_start=context.week_start,
        days=final_days,
        retry_count=retry_count,
        llm_meta=_aggregate_llm_meta(
            weekly_meta, detail_metas=detail_metas, repair_metas=repair_metas
        ),
        prompt_version=prompt_version,
        overall_comment=overall_comment,
    )


@dataclass
class _AttemptDay:
    plan_date: date
    dishes: tuple[NormalizedDish, ...]
    violations: tuple[Violation, ...]
    meta: LLMCallMeta
    overall_comment: str | None


async def _generate_split(
    context: WeeklySuggestContext,
    *,
    candidate_mappings: list[dict[str, Any]],
    recent_names: Sequence[str],
    dictionary: NormalizerDictionary,
    llm: LLMClient,
    detail_llm: LLMClient,
) -> tuple[list[_AttemptDay], str, list[LLMCallMeta]]:
    """分割経路: 骨子 1 回 (llm) + 日別の食材詳細 7 並列 (detail_llm).

    詳細呼び出しが失敗した日は block 違反の空日として返し、後段の単日修復に回す.
    全日失敗した場合のみ ``WeeklyDetailAllFailedError`` を投げる.
    """
    skeleton_system = get_prompt(SKELETON_SYSTEM_PROMPT_NAME)
    skeleton_user = get_prompt(SKELETON_USER_PROMPT_NAME)
    detail_system = get_prompt(DETAIL_SYSTEM_PROMPT_NAME)
    detail_user = get_prompt(DETAIL_USER_PROMPT_NAME)
    prompt_version = f"skeleton={skeleton_system.version}/detail={detail_system.version}"

    allergen_summary = build_allergen_summary(context.family)
    dislike_summary = build_dislike_summary(context.family)

    skeleton_variables: dict[str, Any] = {
        "family_profile": family_to_mapping(context.family),
        "allergen_summary": allergen_summary,
        "dislike_summary": dislike_summary,
        "recent_recipe_names": list(recent_names),
        "week_start_label": _week_start_label(context.week_start),
    }
    skeleton_user_variables = {
        "candidate_recipes": candidate_mappings,
        "pantry": list(context.pantry),
        "user_request": context.user_request,
    }

    skeleton, skeleton_meta = await attempt_with_parse_retry(
        _skeleton_attempt,
        log_name="weekly.skeleton",
        system_prompt=skeleton_system,
        user_prompt=skeleton_user,
        base_variables=skeleton_variables,
        user_variables=skeleton_user_variables,
        llm=llm,
    )

    days_by_offset = {d.day_offset: d for d in skeleton.days}

    # return_exceptions=True は必須. 1 日の詳細失敗で gather が例外を投げると、
    # 骨子が取れている他の日まで巻き込んで週全体がエラーになる.
    detail_results = await asyncio.gather(
        *(
            _detail_day(
                days_by_offset.get(offset),
                offset=offset,
                context=context,
                allergen_summary=allergen_summary,
                dislike_summary=dislike_summary,
                system_prompt=detail_system,
                user_prompt=detail_user,
                dictionary=dictionary,
                llm=detail_llm,
            )
            for offset in range(DAYS_IN_WEEK)
        ),
        return_exceptions=True,
    )

    attempt: list[_AttemptDay] = []
    detail_metas: list[LLMCallMeta] = []
    failed_offsets: list[int] = []
    for offset, result in enumerate(detail_results):
        plan_date = context.week_start + timedelta(days=offset)
        if isinstance(result, BaseException):
            failed_offsets.append(offset)
            detail = str(result) or type(result).__name__
            logger.warning(
                "weekly.detail.failed",
                day_offset=offset,
                plan_date=plan_date.isoformat(),
                error_type=type(result).__name__,
                error=detail,
            )
            attempt.append(
                _AttemptDay(
                    plan_date=plan_date,
                    dishes=(),
                    violations=(
                        Violation(
                            severity="block",
                            member="-",
                            ingredient="-",
                            canonical="-",
                            reason=f"食材リストの生成に失敗しました: {detail}",
                        ),
                    ),
                    meta=skeleton_meta,
                    overall_comment=skeleton.overall_comment,
                )
            )
            continue
        dishes, meta = result
        detail_metas.append(meta)
        attempt.append(
            _AttemptDay(
                plan_date=plan_date,
                dishes=dishes,
                violations=collect_violations(dishes, context.family, dictionary),
                meta=skeleton_meta,
                overall_comment=skeleton.overall_comment,
            )
        )

    if len(failed_offsets) == DAYS_IN_WEEK:
        logger.error("weekly.detail.all_failed", week_start=context.week_start.isoformat())
        raise WeeklyDetailAllFailedError(
            "7 日すべての食材リスト生成に失敗しました。安全な提案を作れませんでした。"
        )
    if failed_offsets:
        logger.warning(
            "weekly.detail.partial_failure",
            failed_count=len(failed_offsets),
            failed_offsets=failed_offsets,
        )

    return attempt, prompt_version, detail_metas


async def _skeleton_attempt(
    *,
    system_prompt: Prompt,
    user_prompt: Prompt,
    base_variables: dict[str, Any],
    user_variables: dict[str, Any],
    llm: LLMClient,
) -> tuple[LLMWeeklySkeleton, LLMCallMeta]:
    """骨子フェーズを 1 回呼ぶ (食材リストなしの 7 日 x 3 品)."""
    response = await llm.generate(
        system=system_prompt.render(**base_variables),
        user=user_prompt.render(**user_variables),
        prompt_version=system_prompt.version,
        max_tokens=_SKELETON_MAX_TOKENS,
        output_schema=LLMWeeklySkeleton,
    )
    try:
        parsed = parse_llm_json(
            response.raw_text,
            model=LLMWeeklySkeleton,
            schema_error_label="週間骨子スキーマ検証失敗",
        )
    except LLMResponseParseError:
        logger.exception("weekly.skeleton.parse_error", raw_preview=response.raw_text[:500])
        raise

    meta = LLMCallMeta(
        model=response.model,
        prompt_version=system_prompt.version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        cache_write_tokens=response.cache_write_tokens,
        latency_ms=response.latency_ms,
    )
    return parsed, meta


async def _detail_day(
    skeleton_day: LLMWeeklySkeletonDay | None,
    *,
    offset: int,
    context: WeeklySuggestContext,
    allergen_summary: list[dict[str, Any]],
    dislike_summary: list[dict[str, Any]],
    system_prompt: Prompt,
    user_prompt: Prompt,
    dictionary: NormalizerDictionary,
    llm: LLMClient,
) -> tuple[tuple[NormalizedDish, ...], LLMCallMeta]:
    """骨子の 1 日分に食材リストを付けて正規化済み Dish に変換する.

    骨子に当該 day_offset が無い場合も含め、失敗は例外で呼出元 (gather) に返す.
    """
    if skeleton_day is None:
        raise LLMResponseParseError(f"骨子の応答に day_offset={offset} が含まれていません")

    dishes_json = json.dumps(
        [
            {
                "index": d.index,
                "name": d.name,
                "category": d.category,
                "main_ingredient": d.main_ingredient,
            }
            for d in skeleton_day.dishes
        ],
        ensure_ascii=False,
    )
    plan_date = context.week_start + timedelta(days=offset)

    return await attempt_with_parse_retry(
        _detail_attempt,
        log_name=f"weekly.detail.day{offset}",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        base_variables={
            "allergen_summary": allergen_summary,
            "dislike_summary": dislike_summary,
        },
        user_variables={
            "plan_date_label": _plan_date_label(plan_date),
            "dishes_json": dishes_json,
            "pantry": list(context.pantry),
        },
        skeleton_day=skeleton_day,
        dictionary=dictionary,
        llm=llm,
    )


async def _detail_attempt(
    *,
    system_prompt: Prompt,
    user_prompt: Prompt,
    base_variables: dict[str, Any],
    user_variables: dict[str, Any],
    skeleton_day: LLMWeeklySkeletonDay,
    dictionary: NormalizerDictionary,
    llm: LLMClient,
) -> tuple[tuple[NormalizedDish, ...], LLMCallMeta]:
    response = await llm.generate(
        system=system_prompt.render(**base_variables),
        user=user_prompt.render(**user_variables),
        prompt_version=system_prompt.version,
        max_tokens=_DETAIL_MAX_TOKENS,
        output_schema=LLMDayDetail,
    )
    try:
        parsed = parse_llm_json(
            response.raw_text,
            model=LLMDayDetail,
            schema_error_label="食材詳細スキーマ検証失敗",
        )
    except LLMResponseParseError:
        logger.exception(
            "weekly.detail.parse_error",
            day_offset=skeleton_day.day_offset,
            raw_preview=response.raw_text[:500],
        )
        raise

    dishes = _merge_day(skeleton_day, parsed, dictionary)
    meta = LLMCallMeta(
        model=response.model,
        prompt_version=system_prompt.version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        cache_write_tokens=response.cache_write_tokens,
        latency_ms=response.latency_ms,
    )
    return dishes, meta


def _merge_day(
    skeleton_day: LLMWeeklySkeletonDay,
    detail: LLMDayDetail,
    dictionary: NormalizerDictionary,
) -> tuple[NormalizedDish, ...]:
    """骨子の dish と詳細の食材リストを ``index`` で突き合わせて正規化する.

    料理名は照合用のエコーとしてのみ検査し、不一致は warn ログに留める.
    表記揺れ (「豚の生姜焼き」と「豚肉の生姜焼き」など) は実 LLM で頻繁に起きるが、
    ``collect_violations`` は日内の全食材を検証するため、紐付けがずれても
    アレルゲン検出には影響しない. ここで block に格上げすると、安全性に無関係な
    理由で高価な単日修復にエスカレーションしてしまう.

    一方、品数の不一致・index の欠落は「どの料理の食材か特定できない」状態なので、
    ``LLMResponseParseError`` として扱い再試行・単日修復に回す (フェイルクローズ).
    """
    if len(detail.dishes) != len(skeleton_day.dishes):
        raise LLMResponseParseError(
            f"食材詳細の品数が骨子と一致しません "
            f"(骨子 {len(skeleton_day.dishes)} 品 / 詳細 {len(detail.dishes)} 品)"
        )

    detail_by_index = {d.index: d for d in detail.dishes}
    merged: list[NormalizedDish] = []
    for skeleton_dish in skeleton_day.dishes:
        detail_dish = detail_by_index.get(skeleton_dish.index)
        if detail_dish is None:
            raise LLMResponseParseError(
                f"食材詳細に index={skeleton_dish.index} ({skeleton_dish.name}) がありません"
            )
        if fold_key(detail_dish.name) != fold_key(skeleton_dish.name):
            logger.warning(
                "weekly.detail.name_mismatch",
                day_offset=skeleton_day.day_offset,
                index=skeleton_dish.index,
                skeleton_name=skeleton_dish.name,
                detail_name=detail_dish.name,
            )
        merged.append(
            normalize_dish(
                LLMDish(
                    name=skeleton_dish.name,
                    category=skeleton_dish.category,
                    main_ingredient=skeleton_dish.main_ingredient,
                    reason=skeleton_dish.reason,
                    ingredients=detail_dish.ingredients,
                    steps=detail_dish.steps,
                ),
                dictionary,
                normalized_dish_cls=NormalizedDish,
            )
        )
    return tuple(merged)


def _week_start_label(week_start: date) -> str:
    return week_start.strftime("%Y年%-m月%-d日")


def _plan_date_label(plan_date: date) -> str:
    return plan_date.strftime("%Y年%-m月%-d日")


async def _repair_day(
    day: _AttemptDay,
    all_days: Sequence[_AttemptDay],
    *,
    context: WeeklySuggestContext,
    dictionary: NormalizerDictionary,
    llm: LLMClient,
) -> _AttemptDay:
    """block 違反が出た 1 日だけを suggest_dinner (単日提案) で作り直す.

    週内の他の 6 日で採用済みの料理名 (1 回目の attempt から取得) と、違反日自身が
    1 回目に出した料理名を recent_history に加えることで、単日提案の事前フィルタで
    どちらも除外し、週内での重複と違反した料理そのものの再提案を防ぐ.
    suggest_dinner は内部で独自に最大 1 回のガードレールリトライを行うため、
    ここでは追加のリトライ制御はしない.

    既知の制約: 複数日を同時に修復する場合、各修復は他の修復結果を知らないため
    修復日同士で料理が重複しうる. 逐次実行すれば防げるが、レイテンシの悪化に
    見合わないため並列のままとする (安全性には影響しない).
    """
    now = datetime.now(UTC)
    other_day_names = [
        dish.name for other in all_days if other.plan_date != day.plan_date for dish in other.dishes
    ]
    own_names = [dish.name for dish in day.dishes]
    extra_history = tuple((name, now) for name in (*other_day_names, *own_names))

    single_context = SuggestContext(
        family=context.family,
        all_recipes=context.all_recipes,
        recent_history=(*context.recent_history, *extra_history),
        pantry=context.pantry,
        user_request=context.user_request,
        recency_days=context.recency_days,
    )
    proposal = await suggest_dinner(single_context, llm=llm, dictionary=dictionary)
    logger.info(
        "weekly.repair.done",
        plan_date=day.plan_date.isoformat(),
        succeeded=proposal.succeeded,
    )
    return _AttemptDay(
        plan_date=day.plan_date,
        dishes=proposal.dishes,
        violations=proposal.violations,
        meta=proposal.llm_meta,
        overall_comment=None,
    )


def _aggregate_llm_meta(
    weekly_meta: LLMCallMeta,
    *,
    detail_metas: Sequence[LLMCallMeta] = (),
    repair_metas: Sequence[LLMCallMeta] = (),
) -> LLMCallMeta:
    """週間 (骨子) 呼び出し・詳細呼び出し・修復呼び出し全体のトークン消費を合算する.

    単日修復への置き換え前は最後の attempt (週全体の再生成) の meta だけを保存して
    おり、1 回目の消費トークンが proposals レコードから消えてコストが最大半分に
    過少計上されていた。ここでは全ての呼び出しの input/output/cache トークンを
    合計する。latency は「骨子 (または週間 1 回) + 詳細の最大値 + 修復の最大値」と
    する。詳細同士・修復同士はそれぞれ並列だが、骨子 -> 詳細 -> 修復は前段の結果を
    見てから次を始める逐次関係にあるため、この和が実際の壁時計時間に近い。
    model / prompt_version は骨子 (週間) 呼び出しのものを代表値として使う。

    分割経路では骨子と詳細でモデルが異なるが、proposals レコードの `model` は
    代表値 1 つしか持たない。モデル別の按分は `llm_usage` の
    purpose="weekly" / "weekly_detail" で確認する。

    以下 3 つのトークンはここでは合算されず、proposals レコード上は過少計上になる。
    いずれも `budget_guard` が呼び出し単位で `llm_usage` に正確に記録しており、
    予算超過の検知はそちらが担うため、実害はコスト表示の精度に留まる。

    - attempt_with_parse_retry によるパース再試行分 (骨子・詳細のいずれも、
      _skeleton_attempt / _detail_attempt の戻り値から取得できない)
    - suggest_dinner が修復内部でガードレールリトライした場合の 1 回目
      (suggest_dinner が最後の attempt の meta しか返さない)
    - 詳細呼び出しが例外で終わった日の消費分 (戻り値が無い)
    """
    extra = [*detail_metas, *repair_metas]
    return LLMCallMeta(
        model=weekly_meta.model,
        prompt_version=weekly_meta.prompt_version,
        input_tokens=weekly_meta.input_tokens + sum(m.input_tokens for m in extra),
        output_tokens=weekly_meta.output_tokens + sum(m.output_tokens for m in extra),
        cache_read_tokens=weekly_meta.cache_read_tokens + sum(m.cache_read_tokens for m in extra),
        cache_write_tokens=weekly_meta.cache_write_tokens
        + sum(m.cache_write_tokens for m in extra),
        latency_ms=weekly_meta.latency_ms
        + max((m.latency_ms for m in detail_metas), default=0)
        + max((m.latency_ms for m in repair_metas), default=0),
    )


def _repair_failure_to_day(
    original: _AttemptDay,
    error: BaseException,
    *,
    prompt_version: str,
) -> _AttemptDay:
    """修復呼び出しが例外で終わった日を block 違反の空日に変換する.

    フェイルオープンしないため dishes は空にする. 例外の内容は違反の reason に
    残して UI から原因が追えるようにする (予算超過ならその案内文がそのまま出る).
    """
    detail = str(error) or type(error).__name__
    logger.warning(
        "weekly.repair.failed",
        plan_date=original.plan_date.isoformat(),
        error_type=type(error).__name__,
        error=detail,
    )
    return _AttemptDay(
        plan_date=original.plan_date,
        dishes=(),
        violations=(
            Violation(
                severity="block",
                member="-",
                ingredient="-",
                canonical="-",
                reason=f"単日提案での修復に失敗しました: {detail}",
            ),
        ),
        meta=_empty_meta(prompt_version),
        overall_comment=None,
    )


def _empty_meta(prompt_version: str) -> LLMCallMeta:
    return LLMCallMeta(
        model="unknown",
        prompt_version=prompt_version,
    )


# 互換用 export (type hint で参照される)
__all__ = [
    "Category",
    "DailyResult",
    "WeeklyDetailAllFailedError",
    "WeeklyDinnerProposal",
    "WeeklySuggestContext",
    "plan_week",
]

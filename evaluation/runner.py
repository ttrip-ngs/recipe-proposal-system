"""ゴールデンセット評価ランナー.

使い方:
    uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl
    uv run python evaluation/runner.py --golden evaluation/golden/allergen_cases.jsonl --live
    uv run python evaluation/runner.py --golden '...*.jsonl' --output evaluation/reports/run.json

MVP 初期版: フェイク LLM で services.suggest_dinner を呼び、expected_properties を検査する.
--live 指定時は Vertex AI 経由で実呼び出し (課金あり)。どのプロバイダ (Claude/Gemini) を
使うかは環境変数 LLM_PROVIDER (未指定時は USE_FAKE_LLM から導出) に従う.
例: ``LLM_PROVIDER=gemini uv run python evaluation/runner.py --golden ... --live``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
# `uv run python evaluation/runner.py` で起動すると sys.path[0] は evaluation/ に
# なるため、`from evaluation.schema import ...` の解決にリポジトリルートが要る.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.schema import ExpectedProperties, GoldenCase, GoldenFamilyProfile  # noqa: E402
from recipe_system.config import get_settings  # noqa: E402
from recipe_system.domain import FamilyMember, FamilyProfile  # noqa: E402
from recipe_system.llm.client import (  # noqa: E402
    FakeVertexClient,
    LLMClient,
    build_raw_client,
    effort_for,
    resolve_detail_model,
)
from recipe_system.llm.factory import Purpose  # noqa: E402
from recipe_system.services.suggest_dinner import (  # noqa: E402
    DinnerProposal,
    SuggestContext,
    suggest_dinner,
)
from recipe_system.services.weekly_planner import (  # noqa: E402
    WeeklyDinnerProposal,
    WeeklySuggestContext,
    plan_week,
)


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    reason: str | None
    violations_count: int
    dishes: list[dict[str, Any]]


@dataclass
class RunReport:
    run_id: str
    prompt_version: str
    total_cases: int
    passed: int
    failed: int
    cases: list[CaseResult]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ゴールデンセット評価")
    parser.add_argument("--golden", type=Path, nargs="+", required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Vertex AI に実呼び出し (未指定時はフェイク)",
    )
    parser.add_argument("--prompt-version", default="unknown")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    with path.open(encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                cases.append(GoldenCase.model_validate_json(line))
            except ValidationError as e:
                raise ValueError(f"{path}:{line_no} スキーマ違反: {e}") from e
    return cases


def _to_family_profile(source: GoldenFamilyProfile) -> FamilyProfile:
    now = datetime.now(UTC)
    members = tuple(
        FamilyMember(
            member_id=f"m-{i}",
            name=m.name,
            allergens=frozenset(m.allergens),
            dislikes=frozenset(m.dislikes),
            reviewed_at=now,
        )
        for i, m in enumerate(source.members)
    )
    return FamilyProfile(
        family_id="golden-family",
        name=source.name,
        allowed_emails=frozenset({"golden@example.com"}),
        members=members,
        created_at=now,
        updated_at=now,
    )


def _recent_history(last_7_days: list[str]) -> list[tuple[str, datetime]]:
    now = datetime.now(UTC)
    return [(name, now - timedelta(days=i + 1)) for i, name in enumerate(last_7_days)]


def _verify(case: GoldenCase, proposal: DinnerProposal) -> tuple[bool, str | None]:
    expected = case.expected_properties

    # must_not_contain_allergen: いずれの dish の ingredient にもヒットしないこと
    forbidden = set(expected.must_not_contain_allergen)
    if forbidden:
        for dish in proposal.dishes:
            for ing in dish.ingredients:
                hit = ing.allergen_tags & forbidden
                if hit:
                    tag = next(iter(hit))
                    return False, (f"禁止アレルゲン '{tag}' が {dish.name}/{ing.name} に含まれる")
        # さらに validator が出した block があっても失敗とする
        blocks = [v for v in proposal.violations if v.severity == "block"]
        if blocks:
            return False, f"ガードレール block: {blocks[0].reason}"

    # must_include_category: 指定カテゴリがすべて揃うこと
    if expected.must_include_category:
        found = {d.category for d in proposal.dishes}
        missing = set(expected.must_include_category) - found
        if missing:
            return False, f"カテゴリ不足: {sorted(missing)}"

    # must_not_include_recent_dishes: 指定料理名が提案に含まれないこと
    forbidden_dishes = set(expected.must_not_include_recent_dishes)
    if forbidden_dishes:
        repeated = {d.name for d in proposal.dishes} & forbidden_dishes
        if repeated:
            return False, f"直近と重複: {sorted(repeated)}"

    # 実行自体が成功していること (block 後リトライして成功扱いになること)
    if not proposal.succeeded:
        return False, "succeeded=False"

    return True, None


def _build_llm(use_live: bool, *, purpose: Purpose, model: str | None = None) -> LLMClient:
    """評価用の生 LLM クライアントを組み立てる (予算ガード・Firestore 記録の対象外).

    --live 未指定なら常に Fake。--live 指定時は settings.effective_llm_provider
    (LLM_PROVIDER または USE_FAKE_LLM から導出) の実プロバイダを使う。
    fake のまま --live された場合は設定ミスなので明示的にエラーにする.
    """
    if not use_live:
        return FakeVertexClient()
    provider = get_settings().effective_llm_provider
    if provider == "fake":
        raise ValueError(
            "--live には LLM_PROVIDER=claude / gemini / anthropic のいずれかの指定が必要です"
            " (現在は fake に解決されています)"
        )
    # effort は本番 (build_client) と同じ用途別の値を使う. 評価だけ推論量が違うと
    # ゴールデンセットの結果が本番の挙動を表さなくなる.
    return build_raw_client(provider, model=model, effort=effort_for(purpose))


async def _evaluate_case(
    case: GoldenCase,
    *,
    use_live: bool,
) -> CaseResult:
    if case.scope == "weekly":
        return await _evaluate_weekly_case(case, use_live=use_live)
    return await _evaluate_single_case(case, use_live=use_live)


async def _evaluate_single_case(
    case: GoldenCase,
    *,
    use_live: bool,
) -> CaseResult:
    family = _to_family_profile(case.input.family_profile)
    history = _recent_history(case.input.history_summary.last_7_days)
    ctx = SuggestContext(
        family=family,
        all_recipes=(),
        recent_history=history,
        pantry=tuple(case.input.pantry),
        user_request=case.input.user_request,
    )

    llm = _build_llm(use_live, purpose="single_day")

    try:
        proposal = await suggest_dinner(ctx, llm=llm)
    except Exception as e:
        return CaseResult(
            case_id=case.case_id,
            passed=False,
            reason=f"例外: {type(e).__name__}: {e}",
            violations_count=0,
            dishes=[],
        )

    passed, reason = _verify(case, proposal)
    return CaseResult(
        case_id=case.case_id,
        passed=passed,
        reason=reason,
        violations_count=len(proposal.violations),
        dishes=[
            {"name": d.name, "category": d.category, "main_ingredient": d.main_ingredient}
            for d in proposal.dishes
        ],
    )


async def _evaluate_weekly_case(
    case: GoldenCase,
    *,
    use_live: bool,
) -> CaseResult:
    from datetime import date as _date

    family = _to_family_profile(case.input.family_profile)
    last_14 = case.input.history_summary.last_14_days or case.input.history_summary.last_7_days
    history = _recent_history(last_14)
    week_start_str = case.input.week_start
    if not week_start_str:
        return CaseResult(
            case_id=case.case_id,
            passed=False,
            reason="weekly ケースは input.week_start が必須",
            violations_count=0,
            dishes=[],
        )
    try:
        week_start = _date.fromisoformat(week_start_str)
    except ValueError:
        return CaseResult(
            case_id=case.case_id,
            passed=False,
            reason=f"week_start が不正: {week_start_str!r}",
            violations_count=0,
            dishes=[],
        )

    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=(),
        recent_history=history,
        week_start=week_start,
        pantry=tuple(case.input.pantry),
        user_request=case.input.user_request,
    )
    llm = _build_llm(use_live, purpose="weekly")
    detail_llm = _build_llm(use_live, purpose="weekly_detail", model=resolve_detail_model())
    try:
        proposal = await plan_week(ctx, llm=llm, detail_llm=detail_llm)
    except Exception as e:
        return CaseResult(
            case_id=case.case_id,
            passed=False,
            reason=f"例外: {type(e).__name__}: {e}",
            violations_count=0,
            dishes=[],
        )

    passed, reason = _verify_weekly(case, proposal)
    return CaseResult(
        case_id=case.case_id,
        passed=passed,
        reason=reason,
        violations_count=sum(len(d.violations) for d in proposal.days),
        dishes=[
            {
                "plan_date": day.plan_date.isoformat(),
                "succeeded": day.succeeded,
                "dishes": [
                    {"name": d.name, "category": d.category, "main_ingredient": d.main_ingredient}
                    for d in day.dishes
                ],
            }
            for day in proposal.days
        ],
    )


def _verify_weekly(case: GoldenCase, proposal: WeeklyDinnerProposal) -> tuple[bool, str | None]:
    expected = case.expected_properties
    for check in (
        _check_weekly_day_count,
        _check_weekly_allergens,
        _check_weekly_no_recent,
        _check_weekly_categories,
        _check_weekly_main_variation,
    ):
        result = check(expected, proposal)
        if result is not None:
            return False, result
    if not proposal.all_succeeded:
        failed = [d.plan_date.isoformat() for d in proposal.days if not d.succeeded]
        return False, f"失敗した日が残った: {failed}"
    return True, None


def _check_weekly_day_count(
    expected: ExpectedProperties, proposal: WeeklyDinnerProposal
) -> str | None:
    if expected.must_have_days is None or len(proposal.days) == expected.must_have_days:
        return None
    return f"日数不一致: 期待={expected.must_have_days} 実際={len(proposal.days)}"


def _check_weekly_allergens(
    expected: ExpectedProperties, proposal: WeeklyDinnerProposal
) -> str | None:
    forbidden = set(expected.must_not_contain_allergen)
    if not forbidden:
        return None
    for day in proposal.days:
        for dish in day.dishes:
            for ing in dish.ingredients:
                hit = ing.allergen_tags & forbidden
                if hit:
                    tag = next(iter(hit))
                    detail = f"{dish.name}/{ing.name}"
                    return f"day={day.plan_date}: 禁止アレルゲン '{tag}' が {detail} に含まれる"
    return None


def _check_weekly_no_recent(
    expected: ExpectedProperties, proposal: WeeklyDinnerProposal
) -> str | None:
    forbidden_dishes = set(expected.must_not_include_recent_dishes)
    if not forbidden_dishes:
        return None
    for day in proposal.days:
        repeated = {d.name for d in day.dishes} & forbidden_dishes
        if repeated:
            return f"day={day.plan_date}: 直近と重複 {sorted(repeated)}"
    return None


def _check_weekly_categories(
    expected: ExpectedProperties, proposal: WeeklyDinnerProposal
) -> str | None:
    if not expected.must_include_category_per_day:
        return None
    required = set(expected.must_include_category_per_day)
    for day in proposal.days:
        if not day.succeeded:
            continue
        missing = required - {d.category for d in day.dishes}
        if missing:
            return f"day={day.plan_date}: カテゴリ不足 {sorted(missing)}"
    return None


def _check_weekly_main_variation(
    expected: ExpectedProperties, proposal: WeeklyDinnerProposal
) -> str | None:
    if not expected.must_vary_main_ingredient_within_consecutive_days:
        return None
    prev_mains: set[str] = set()
    prev_date = None
    for day in proposal.days:
        if not day.succeeded:
            prev_mains = set()
            prev_date = day.plan_date
            continue
        mains = {d.main_ingredient for d in day.dishes if d.category == "主菜"}
        if prev_mains and mains & prev_mains:
            overlap = sorted(mains & prev_mains)
            return f"day={day.plan_date} と前日 {prev_date} で主食材が重複 ({overlap})"
        prev_mains = mains
        prev_date = day.plan_date
    return None


async def _run(cases: list[GoldenCase], *, use_live: bool) -> list[CaseResult]:
    return [await _evaluate_case(c, use_live=use_live) for c in cases]


def main() -> int:
    args = _parse_args()

    cases: list[GoldenCase] = []
    for path in args.golden:
        cases.extend(_load_jsonl(path))
    if not cases:
        print("[warn] 評価ケースが 0 件", file=sys.stderr)
        return 1

    print(f"[mode] {'live' if args.live else 'fake-llm'} / total={len(cases)}", file=sys.stderr)
    results = asyncio.run(_run(cases, use_live=args.live))
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    report = RunReport(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        prompt_version=args.prompt_version,
        total_cases=len(results),
        passed=passed,
        failed=failed,
        cases=results,
    )

    report_dict = asdict(report)
    print(json.dumps(report_dict, ensure_ascii=False, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=2)
        print(f"[written] {args.output}", file=sys.stderr)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

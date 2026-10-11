"""ゴールデンセット評価ランナー.

使い方:
    uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl
    uv run python evaluation/runner.py --golden evaluation/golden/allergen_cases.jsonl --live
    uv run python evaluation/runner.py --golden '...*.jsonl' --output evaluation/reports/run.json

既定はフェイク LLM で services.suggest_dinner / plan_week を呼び、expected_properties を
検査する (API 料金なし. CI の golden-eval ジョブはこのモード). レポートには
docs/evaluation.md §6 のメトリクス (guard_trigger_rate / warn_rate /
unknown_ingredient_rate) を出す.
--live 指定時は Vertex AI 経由で実呼び出し (課金あり)。どのプロバイダ (Claude/Gemini) を
使うかは環境変数 LLM_PROVIDER (未指定時は USE_FAKE_LLM から導出) に従う.
例: ``LLM_PROVIDER=gemini uv run python evaluation/runner.py --golden ... --live``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
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
from recipe_system.domain import FamilyMember, FamilyProfile, Violation  # noqa: E402
from recipe_system.guardrails.dictionary_loader import default_dictionary  # noqa: E402
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
    NormalizedDish,
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
    # 以下はメトリクス算出用. 例外で提案自体が得られなかったケースは has_proposal=False.
    has_proposal: bool = False
    # 初回の提案がガードレールで block され、再依頼した (retry_count >= 1)
    guard_triggered: bool = False
    # 最終提案 (週間は全日) に付いた warn 違反の件数
    warn_count: int = 0
    # 食材正規化辞書に未登録の食材名 (重複なし). 辞書追加の週次レビュー用
    unknown_ingredients: list[str] = field(default_factory=list)


@dataclass
class Metrics:
    """docs/evaluation.md §6 のメトリクス.

    分母は提案が得られたケース数 (1 ケース = 1 提案. 週間は 7 日分で 1 提案).
    例外で提案が得られなかったケースは不合格として数えるが、分母には含めない.
    提案が 0 件のときは各率を 0.0 とする.
    """

    proposals: int
    guard_trigger_rate: float
    warn_rate: float
    unknown_ingredient_rate: float


@dataclass
class RunReport:
    run_id: str
    prompt_version: str
    total_cases: int
    passed: int
    failed: int
    metrics: Metrics
    cases: list[CaseResult]


def compute_metrics(results: Iterable[CaseResult]) -> Metrics:
    proposals = [r for r in results if r.has_proposal]

    def rate(count: int) -> float:
        return count / len(proposals) if proposals else 0.0

    return Metrics(
        proposals=len(proposals),
        guard_trigger_rate=rate(sum(1 for r in proposals if r.guard_triggered)),
        warn_rate=rate(sum(1 for r in proposals if r.warn_count > 0)),
        unknown_ingredient_rate=rate(sum(1 for r in proposals if r.unknown_ingredients)),
    )


def _warn_count(violations: Iterable[Violation]) -> int:
    return sum(1 for v in violations if v.severity == "warn")


def _unknown_ingredients(dishes: Iterable[NormalizedDish]) -> list[str]:
    dictionary = default_dictionary()
    names = {ing.name for dish in dishes for ing in dish.ingredients}
    return sorted(name for name in names if not dictionary.is_known(name))


def render_summary(report: RunReport, *, mode: str) -> str:
    """GitHub Actions の job summary 用 Markdown (合否・メトリクス・不合格ケース)."""
    m = report.metrics
    lines = [
        f"## ゴールデンセット評価 ({mode})",
        "",
        f"合格 {report.passed} / {report.total_cases} (不合格 {report.failed})",
        "",
        "| metric | value |",
        "|---|---|",
        f"| proposals | {m.proposals} |",
        f"| guard_trigger_rate | {m.guard_trigger_rate:.3f} |",
        f"| warn_rate | {m.warn_rate:.3f} |",
        f"| unknown_ingredient_rate | {m.unknown_ingredient_rate:.3f} |",
    ]
    failed = [c for c in report.cases if not c.passed]
    if failed:
        lines += ["", "### 不合格ケース", "", "| case | reason |", "|---|---|"]
        lines += [f"| {c.case_id} | {_table_cell(c.reason or '')} |" for c in failed]
    unknown = sorted({name for c in report.cases for name in c.unknown_ingredients})
    if unknown:
        lines += ["", f"未知食材: {', '.join(unknown)}"]
    return "\n".join(lines) + "\n"


def _table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


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
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="結果の Markdown を追記するファイル (CI では $GITHUB_STEP_SUMMARY を渡す)",
    )
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
        has_proposal=True,
        guard_triggered=proposal.retry_count > 0,
        warn_count=_warn_count(proposal.violations),
        unknown_ingredients=_unknown_ingredients(proposal.dishes),
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
    # 詳細モデルの解決は設定 (.env) を読むため --live 時に限る. Fake 評価は CI の
    # ように .env の無い環境でも動く必要がある.
    detail_model = resolve_detail_model() if use_live else None
    detail_llm = _build_llm(use_live, purpose="weekly_detail", model=detail_model)
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
        has_proposal=True,
        guard_triggered=proposal.retry_count > 0,
        warn_count=_warn_count(v for day in proposal.days for v in day.violations),
        # block が残った日は dishes が空のため、確定した日の食材だけが対象になる
        unknown_ingredients=_unknown_ingredients(d for day in proposal.days for d in day.dishes),
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
        metrics=compute_metrics(results),
        cases=results,
    )

    report_dict = asdict(report)
    print(json.dumps(report_dict, ensure_ascii=False, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=2)
        print(f"[written] {args.output}", file=sys.stderr)

    if args.summary:
        with args.summary.open("a", encoding="utf-8") as f:
            f.write(render_summary(report, mode="live" if args.live else "fake LLM"))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

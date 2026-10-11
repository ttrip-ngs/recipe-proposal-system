"""evaluation/runner.py の end-to-end 動作確認.

Firestore は不要 (ゴールデンケース + フェイク LLM で完結する).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _clear_settings_caches() -> None:
    """get_settings の lru_cache を空にする.

    ``recipe_system.llm.client`` は import 時に get_settings を束縛するため、
    ``config`` 側のキャッシュだけでなく client 側もクリアする必要がある
    (詳細は test_client_factory.py の同名関数コメント参照).
    """
    from recipe_system import config
    from recipe_system.llm import client as llm_client_module

    config.get_settings.cache_clear()
    llm_client_module.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    _clear_settings_caches()
    yield
    _clear_settings_caches()


@pytest.mark.asyncio
async def test_ゴールデンセットでアレルゲン違反が検出されないこと() -> None:
    from evaluation.runner import _evaluate_case, _load_jsonl

    cases_path = REPO_ROOT / "evaluation" / "golden" / "allergen_cases.jsonl"
    cases = _load_jsonl(cases_path)
    assert cases, "allergen_cases.jsonl が空"

    results = [await _evaluate_case(c, use_live=False) for c in cases]

    # デフォルトフェイク LLM の応答には卵 / 甲殻類 / ナッツ / 落花生が含まれないため
    # allergen_cases は全て pass するはず.
    failed = [r for r in results if not r.passed]
    assert not failed, f"想定外の失敗: {[(r.case_id, r.reason) for r in failed]}"


def test_runner_main_のインタフェースが壊れていない() -> None:
    from evaluation.runner import _load_jsonl

    cases_path = REPO_ROOT / "evaluation" / "golden" / "allergen_cases.jsonl"
    cases = _load_jsonl(cases_path)
    assert len(cases) >= 3


def test_build_llm_は_live_未指定なら_Fake(monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation.runner import _build_llm

    from recipe_system.llm.client import FakeVertexClient

    monkeypatch.setenv("USE_FAKE_LLM", "false")  # --live なしなら無視される
    assert isinstance(_build_llm(use_live=False, purpose="weekly"), FakeVertexClient)


def test_build_llm_は_live_かつ_provider_が_fake_解決なら_ValueError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.runner import _build_llm

    monkeypatch.setenv("USE_FAKE_LLM", "true")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        _build_llm(use_live=True, purpose="weekly")


def test_build_llm_は_live_かつ_LLM_PROVIDER_gemini_なら_VertexGeminiClient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.runner import _build_llm

    from recipe_system.llm.client import VertexGeminiClient

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert isinstance(_build_llm(use_live=True, purpose="weekly"), VertexGeminiClient)


def _golden_case(member: dict[str, Any]) -> Any:
    from evaluation.schema import GoldenCase

    return GoldenCase.model_validate(
        {
            "case_id": "T001",
            "input": {"family_profile": {"members": [member]}},
            "expected_properties": {"must_include_category": ["主菜"]},
        }
    )


def _main_dish(name: str, ingredients: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "category": "主菜",
        "main_ingredient": ingredients[0],
        "reason": "test",
        "ingredients": [{"name": i, "quantity": 1, "unit": "個"} for i in ingredients],
        "steps": ["加熱する"],
    }


class _ScriptedClient:
    """呼出ごとに用意した応答を順に返す LLM."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads

    async def generate(self, **_: Any) -> Any:
        from recipe_system.llm.client import LLMResponse

        return LLMResponse(raw_text=json.dumps(self._payloads.pop(0)), model="scripted")


def test_compute_metrics_は提案の得られたケースを分母にする() -> None:
    from evaluation.runner import CaseResult, compute_metrics

    def result(case_id: str, **kwargs: Any) -> CaseResult:
        return CaseResult(
            case_id=case_id, passed=True, reason=None, violations_count=0, dishes=[], **kwargs
        )

    metrics = compute_metrics(
        [
            result("A", has_proposal=True, guard_triggered=True),
            result("B", has_proposal=True, warn_count=2, unknown_ingredients=["謎の食材"]),
            result("C", has_proposal=True),
            result("D", has_proposal=True, warn_count=1),
            # 例外で提案が無いケースは分母に入れない
            result("E", has_proposal=False, guard_triggered=True),
        ]
    )

    assert metrics.proposals == 4
    assert metrics.guard_trigger_rate == 0.25
    assert metrics.warn_rate == 0.5
    assert metrics.unknown_ingredient_rate == 0.25


def test_compute_metrics_は提案0件なら0() -> None:
    from evaluation.runner import compute_metrics

    metrics = compute_metrics([])
    assert metrics.proposals == 0
    assert metrics.guard_trigger_rate == 0.0
    assert metrics.warn_rate == 0.0
    assert metrics.unknown_ingredient_rate == 0.0


@pytest.mark.asyncio
async def test_初回_block_で再依頼したケースは_guard_triggered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation import runner

    client = _ScriptedClient(
        [
            {"dishes": [_main_dish("オムレツ", ["卵"])]},
            {"dishes": [_main_dish("鮭の塩焼き", ["鮭"])]},
        ]
    )
    monkeypatch.setattr(runner, "_build_llm", lambda *_, **__: client)

    result = await runner._evaluate_case(
        _golden_case({"name": "子", "allergens": ["卵"]}), use_live=False
    )

    assert result.passed, result.reason
    assert result.has_proposal
    assert result.guard_triggered
    assert result.warn_count == 0


@pytest.mark.asyncio
async def test_嫌いな食材の_warn_と未知食材を記録する(monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation import runner

    client = _ScriptedClient(
        [{"dishes": [_main_dish("豚肉とにんじんの炒め物", ["豚肉", "にんじん", "謎の香草"])]}]
    )
    monkeypatch.setattr(runner, "_build_llm", lambda *_, **__: client)

    result = await runner._evaluate_case(
        _golden_case({"name": "夫", "dislikes": ["にんじん"]}), use_live=False
    )

    assert result.passed, result.reason
    assert not result.guard_triggered
    assert result.warn_count >= 1
    assert result.unknown_ingredients == ["謎の香草"]


@pytest.mark.asyncio
async def test_フェイク_LLM_でゴールデンセット全件が合格する() -> None:
    """CI の golden-eval ジョブと同じ条件. 直近重複 (G101) などの回帰を pytest でも検出する."""
    from evaluation.runner import _evaluate_case, _load_jsonl, compute_metrics

    cases = [
        case
        for path in sorted((REPO_ROOT / "evaluation" / "golden").glob("*.jsonl"))
        for case in _load_jsonl(path)
    ]
    results = [await _evaluate_case(c, use_live=False) for c in cases]

    failed = [(r.case_id, r.reason) for r in results if not r.passed]
    assert not failed
    metrics = compute_metrics(results)
    assert metrics.proposals == len(cases)
    # フェイクの献立は辞書登録済みの食材のみで、アレルゲン・嫌いな食材も含まない
    assert metrics.guard_trigger_rate == 0.0
    assert metrics.unknown_ingredient_rate == 0.0


def test_render_summary_はメトリクスと不合格ケースを出す() -> None:
    from evaluation.runner import CaseResult, RunReport, compute_metrics, render_summary

    cases = [
        CaseResult("G001", True, None, 0, [], has_proposal=True, unknown_ingredients=["謎の香草"]),
        CaseResult("G101", False, "直近と重複: ['肉じゃが'] | x", 0, [], has_proposal=True),
    ]
    report = RunReport(
        run_id="r",
        prompt_version="p",
        total_cases=2,
        passed=1,
        failed=1,
        metrics=compute_metrics(cases),
        cases=cases,
    )

    summary = render_summary(report, mode="fake LLM")

    assert "## ゴールデンセット評価 (fake LLM)" in summary
    assert "合格 1 / 2 (不合格 1)" in summary
    assert "| unknown_ingredient_rate | 0.500 |" in summary
    assert "| G101 | 直近と重複: ['肉じゃが'] \\| x |" in summary
    assert "未知食材: 謎の香草" in summary

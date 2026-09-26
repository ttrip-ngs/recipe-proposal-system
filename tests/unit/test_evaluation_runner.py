"""evaluation/runner.py の end-to-end 動作確認.

Firestore は不要 (ゴールデンケース + フェイク LLM で完結する).
"""

from __future__ import annotations

from pathlib import Path

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

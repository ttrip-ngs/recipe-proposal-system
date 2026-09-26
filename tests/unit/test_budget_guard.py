"""`recipe_system.llm.budget_guard` のテスト.

repository 層は monkeypatch で差し替え、inner LLM クライアントは Fake で代用する.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from google.cloud import firestore
from pydantic import BaseModel

from recipe_system.llm.budget_guard import BudgetGuardedClient
from recipe_system.llm.client import FakeVertexClient, LLMResponse
from recipe_system.llm.errors import BudgetExceededError


class _RaisingClient:
    """API 呼出で例外を投げる inner. finally で記録経路に乗ることを検証する."""

    async def generate(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        # 引数は使わない (シグネチャ整合のみ) ことを明示する.
        _ = (system, user, prompt_version, max_tokens, output_schema)
        raise RuntimeError("vertex offline")


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
    *,
    budget: float | None = 1000.0,
    current: float | None = 0.0,
    append_sink: list[Any] | None = None,
    budget_raises: bool = False,
    current_raises: bool = False,
) -> None:
    """budget_guard モジュールから参照される関数を差し替える."""
    from recipe_system.llm import budget_guard

    def _get_budget_jpy(_client: Any) -> float:
        if budget_raises:
            raise RuntimeError("firestore down (budget)")
        assert budget is not None
        return budget

    def _monthly_total_jpy(_client: Any, *, family_id: str, year_month: str) -> float:
        if current_raises:
            raise RuntimeError("firestore down (usage)")
        assert current is not None
        assert family_id
        assert year_month
        return current

    def _append_usage(_client: Any, record: Any) -> None:
        if append_sink is not None:
            append_sink.append(record)

    monkeypatch.setattr(budget_guard, "get_budget_jpy", _get_budget_jpy)
    monkeypatch.setattr(budget_guard, "monthly_total_jpy", _monthly_total_jpy)
    monkeypatch.setattr(budget_guard, "append_usage", _append_usage)


def _make_guarded(inner: Any = None) -> BudgetGuardedClient:
    """budget_guard 内の Firestore 操作は monkeypatch で吸収するため、
    型整合だけ通せばよく実装は呼ばれない. ``cast`` で placeholder を渡す."""
    fake_fs = cast(firestore.Client, object())
    return BudgetGuardedClient(
        inner or FakeVertexClient(),
        purpose="single_day",
        family_id="family-test",
        firestore_client=fake_fs,
        usd_jpy_rate=150.0,
    )


def test_予算内なら_inner_を呼んで_LLMResponse_を返す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink: list[Any] = []
    _patch_repos(monkeypatch, budget=1000.0, current=0.0, append_sink=sink)

    guarded = _make_guarded()
    response = asyncio.run(
        guarded.generate(system="sys", user="usr", prompt_version="v1", max_tokens=100)
    )

    assert response.model == FakeVertexClient.MODEL_NAME
    # 成功 1 件記録される
    assert len(sink) == 1
    assert sink[0].success is True
    assert sink[0].family_id == "family-test"
    assert sink[0].purpose == "single_day"


def test_累積コストが上限以上なら_BudgetExceededError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink: list[Any] = []
    # 累積 = 上限なら block (>=)
    _patch_repos(monkeypatch, budget=500.0, current=500.0, append_sink=sink)

    guarded = _make_guarded()
    with pytest.raises(BudgetExceededError):
        asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))

    # Preflight ブロックなので記録されない
    assert sink == []


def test_予算照会失敗時も_block_to_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    sink: list[Any] = []
    _patch_repos(monkeypatch, budget_raises=True, append_sink=sink)

    guarded = _make_guarded()
    with pytest.raises(BudgetExceededError):
        asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))

    assert sink == []


def test_使用量照会失敗も_block_to_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    sink: list[Any] = []
    _patch_repos(monkeypatch, budget=1000.0, current_raises=True, append_sink=sink)

    guarded = _make_guarded()
    with pytest.raises(BudgetExceededError):
        asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))

    assert sink == []


def test_API_呼出で例外発生でも_finally_で記録される(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inner.generate が例外を投げても usage は success=False で 1 件記録される."""
    sink: list[Any] = []
    _patch_repos(monkeypatch, budget=1000.0, current=0.0, append_sink=sink)

    guarded = _make_guarded(inner=_RaisingClient())
    with pytest.raises(RuntimeError):
        asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))

    assert len(sink) == 1
    assert sink[0].success is False
    assert sink[0].error_code == "RuntimeError"
    assert sink[0].input_tokens == 0


def test_Fake_LLM_使用時はコスト_0_で記録される(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake 実装は PRICING_TABLE 未登録モデルなので cost_jpy=0.0."""
    sink: list[Any] = []
    _patch_repos(monkeypatch, budget=1000.0, current=0.0, append_sink=sink)

    guarded = _make_guarded()
    asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))

    assert sink[0].cost_jpy == 0.0
    assert sink[0].model == FakeVertexClient.MODEL_NAME


def test_記録失敗は本処理に伝搬しない(monkeypatch: pytest.MonkeyPatch) -> None:
    """append_usage が例外を投げても generate 自体は成功して LLMResponse を返す."""
    from recipe_system.llm import budget_guard

    def _raising_append(_client: Any, _record: Any) -> None:
        raise RuntimeError("usage write failed")

    _patch_repos(monkeypatch, budget=1000.0, current=0.0)
    monkeypatch.setattr(budget_guard, "append_usage", _raising_append)

    guarded = _make_guarded()
    response = asyncio.run(guarded.generate(system="s", user="u", prompt_version="v1"))
    assert response.model == FakeVertexClient.MODEL_NAME

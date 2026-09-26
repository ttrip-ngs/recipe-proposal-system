"""`recipe_system.llm.client` のプロバイダ選択 (build_raw_client / build_client) のテスト."""

from __future__ import annotations

import pytest

from recipe_system.llm.factory import Purpose


def _clear_settings_caches() -> None:
    """get_settings の lru_cache を空にする.

    ``recipe_system.llm.client`` は ``from recipe_system.config import get_settings``
    でモジュールレベルに関数オブジェクトを束縛している。他のテスト
    (test_config.py) が ``importlib.reload(config)`` すると config 側は
    新しい get_settings に差し替わるが、既に import 済みの llm.client は
    reload 前の古い関数オブジェクト (=別の lru_cache) を握ったままになる。
    両方のキャッシュを明示的にクリアしないと、テスト実行順序次第で
    env var 変更が反映されない場合があるため、両方をクリアする.
    """
    from recipe_system import config
    from recipe_system.llm import client as llm_client_module

    config.get_settings.cache_clear()
    llm_client_module.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """各テスト前後で lru_cache を空にする (config.get_settings)."""
    _clear_settings_caches()
    yield
    _clear_settings_caches()


def test_build_raw_client_provider_fake_なら_FakeVertexClient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import FakeVertexClient, build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "true")
    client = build_raw_client("fake")
    assert isinstance(client, FakeVertexClient)


def test_build_raw_client_provider_claude_なら_VertexClaudeClient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import VertexClaudeClient, build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "false")
    client = build_raw_client("claude")
    assert isinstance(client, VertexClaudeClient)


def test_build_raw_client_provider_gemini_なら_VertexGeminiClient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import VertexGeminiClient, build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "false")
    client = build_raw_client("gemini")
    assert isinstance(client, VertexGeminiClient)
    assert client._model == VertexGeminiClient.DEFAULT_MODEL


def test_build_raw_client_provider_gemini_かつ_LLM_MODEL_指定でモデル上書き(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "false")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
    client = build_raw_client("gemini")
    assert client._model == "gemini-2.5-pro"


def test_build_raw_client_provider_anthropic_なら_AnthropicDirectClient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import AnthropicDirectClient, build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    client = build_raw_client("anthropic")
    assert isinstance(client, AnthropicDirectClient)
    assert client._model == AnthropicDirectClient.DEFAULT_MODEL


def test_build_raw_client_provider_省略時は_settings_の_effective_llm_provider_に従う(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import FakeVertexClient, build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "true")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    client = build_raw_client()
    assert isinstance(client, FakeVertexClient)


def test_build_client_は_BudgetGuardedClient_でラップする(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.budget_guard import BudgetGuardedClient
    from recipe_system.llm.client import build_client
    from recipe_system.repository import firestore_client

    def _fake_get_firestore_client() -> object:
        return object()

    monkeypatch.setenv("USE_FAKE_LLM", "true")
    monkeypatch.setattr(firestore_client, "get_firestore_client", _fake_get_firestore_client)
    client = build_client(purpose="single_day", family_id="family-test")
    assert isinstance(client, BudgetGuardedClient)


def test_resolve_detail_model_は既定で骨子と同じモデル(monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku 4.5 は食材リストの精度が足りなかったため、既定では分担しない (memo/history/026)."""
    from recipe_system.llm.client import resolve_detail_model

    monkeypatch.delenv("LLM_DETAIL_MODEL", raising=False)
    assert resolve_detail_model() is None


def test_LLM_DETAIL_MODEL_で詳細フェーズのモデルを上書きできる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.client import resolve_detail_model

    monkeypatch.setenv("LLM_DETAIL_MODEL", "claude-sonnet-5")
    assert resolve_detail_model() == "claude-sonnet-5"


@pytest.mark.parametrize(
    ("purpose", "effort"),
    [("single_day", "medium"), ("weekly", "medium"), ("weekly_detail", "low")],
)
def test_build_client_は用途別の_effort_で_anthropic_クライアントを作る(
    monkeypatch: pytest.MonkeyPatch, purpose: Purpose, effort: str
) -> None:
    """献立を決める呼出は medium、決まった料理の食材と手順を書く詳細フェーズは low."""
    from recipe_system.llm.client import build_client
    from recipe_system.repository import firestore_client

    def _fake_get_firestore_client() -> object:
        return object()

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(firestore_client, "get_firestore_client", _fake_get_firestore_client)
    client = build_client(purpose=purpose, family_id="family-test")

    assert client._inner._model == "claude-opus-5-5"
    assert client._inner._effort == effort


def test_build_detail_client_は_weekly_detail_purpose_で記録する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm.budget_guard import BudgetGuardedClient
    from recipe_system.llm.client import build_detail_client
    from recipe_system.repository import firestore_client

    def _fake_get_firestore_client() -> object:
        return object()

    monkeypatch.setenv("USE_FAKE_LLM", "true")
    monkeypatch.setattr(firestore_client, "get_firestore_client", _fake_get_firestore_client)
    client = build_detail_client(family_id="family-test")

    assert isinstance(client, BudgetGuardedClient)
    assert client._purpose == "weekly_detail"


def test_build_raw_client_の_model_引数は_LLM_MODEL_より優先される(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_DETAIL_MODEL で詳細フェーズだけ別モデルにするため、明示指定が env 上書きに勝つ."""
    from recipe_system.llm.client import build_raw_client

    monkeypatch.setenv("USE_FAKE_LLM", "false")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
    client = build_raw_client("gemini", model="gemini-2.5-flash-lite")
    assert client._model == "gemini-2.5-flash-lite"

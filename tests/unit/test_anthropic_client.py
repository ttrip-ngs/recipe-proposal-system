"""`recipe_system.llm.client.AnthropicDirectClient` のテスト.

anthropic SDK は実際にはクライアント構築 (ネットワーク呼出なし) までは許容し、
`generate()` 内の `messages.create` だけをモックに差し替える.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest


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


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


@dataclass
class _FakeTextBlock:
    text: str


class _FakeResponse:
    def __init__(self, text: str, model: str, usage: _FakeUsage) -> None:
        self.content = [_FakeTextBlock(text=text)]
        self.model = model
        self.usage = usage


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
    model: str | None = None,
    effort: Any = None,
) -> Any:
    """messages.create の呼出引数を記録しつつ response を返す AnthropicDirectClient を作る."""
    _base_env(monkeypatch)

    from recipe_system.llm.client import AnthropicDirectClient

    client = AnthropicDirectClient(model=model, effort=effort)

    captured: dict[str, Any] = {}

    async def _fake_create(
        *,
        model: str,
        max_tokens: int,
        system: Any,
        messages: list[dict[str, Any]],
        output_config: Any,
    ) -> _FakeResponse:
        captured["model"] = model
        captured["max_tokens"] = max_tokens
        captured["system"] = system
        captured["messages"] = messages
        captured["output_config"] = output_config
        return response

    monkeypatch.setattr(client._client.messages, "create", _fake_create)
    return client, captured


def test_ANTHROPIC_API_KEY_未設定なら_ValueError(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from recipe_system.llm.client import AnthropicDirectClient

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicDirectClient()


def test_ANTHROPIC_API_KEY_空文字なら_ValueError(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from recipe_system.llm.client import AnthropicDirectClient

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicDirectClient()


def test_generate_は_USE_PROMPT_CACHE_true_なら_cache_control_付き(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_PROMPT_CACHE", "true")
    response = _FakeResponse(
        text="{}",
        model="claude-sonnet-4-6",
        usage=_FakeUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    client, captured = _make_client(monkeypatch, response)

    asyncio.run(client.generate(system="sys-prompt", user="user-prompt", prompt_version="v1"))

    system_payload = captured["system"]
    assert system_payload == [
        {"type": "text", "text": "sys-prompt", "cache_control": {"type": "ephemeral"}}
    ]
    assert captured["messages"] == [{"role": "user", "content": "user-prompt"}]


def test_generate_は_USE_PROMPT_CACHE_false_なら_cache_control_なし(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_PROMPT_CACHE", "false")
    response = _FakeResponse(
        text="{}",
        model="claude-sonnet-4-6",
        usage=_FakeUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    client, captured = _make_client(monkeypatch, response)

    asyncio.run(client.generate(system="sys-prompt", user="user-prompt", prompt_version="v1"))

    system_payload = captured["system"]
    assert system_payload == [{"type": "text", "text": "sys-prompt"}]


def test_generate_は_usage_の_4種のトークンを正しくマッピングする(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(
        text='{"dishes": []}',
        model="claude-sonnet-4-6",
        usage=_FakeUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=40,
            cache_creation_input_tokens=20,
        ),
    )
    client, _ = _make_client(monkeypatch, response)

    result = asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))

    assert result.raw_text == '{"dishes": []}'
    assert result.model == "claude-sonnet-4-6"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 40
    assert result.cache_write_tokens == 20


def test_モデル解決順は_コンストラクタ引数_が最優先(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")

    from recipe_system.llm.client import AnthropicDirectClient

    client = AnthropicDirectClient(model="claude-explicit")
    assert client._model == "claude-explicit"


def test_モデル解決順は_次に_LLM_MODEL_環境変数(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")

    from recipe_system.llm.client import AnthropicDirectClient

    client = AnthropicDirectClient()
    assert client._model == "claude-opus-4-8"


def test_モデル解決順は_最後に_DEFAULT_MODEL(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    from recipe_system.llm.client import AnthropicDirectClient

    client = AnthropicDirectClient()
    assert client._model == AnthropicDirectClient.DEFAULT_MODEL


def _ok_response() -> _FakeResponse:
    return _FakeResponse(
        text="{}",
        model="claude-sonnet-5",
        usage=_FakeUsage(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def test_effort_を指定すると_output_config_で送る(monkeypatch: pytest.MonkeyPatch) -> None:
    client, captured = _make_client(monkeypatch, _ok_response(), effort="low")

    asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))

    assert captured["output_config"] == {"effort": "low"}


def test_effort_も_output_schema_も無ければ_output_config_を送らない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthropic import omit

    client, captured = _make_client(monkeypatch, _ok_response())

    asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))

    assert captured["output_config"] is omit


def test_output_schema_を渡すと構造化出力のスキーマが送られる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.domain.llm_output import LLMDayDetail

    client, captured = _make_client(monkeypatch, _ok_response(), effort="low")

    asyncio.run(
        client.generate(system="s", user="u", prompt_version="v1", output_schema=LLMDayDetail)
    )

    config = captured["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    ingredient = config["format"]["schema"]["$defs"]["LLMIngredient"]
    # 実 Haiku が返した "quantity": 大さじ のような非数値を制約付きデコードで排除する.
    assert ingredient["properties"]["quantity"]["anyOf"] == [{"type": "number"}, {"type": "null"}]


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-haiku-4-5-20251001"])
def test_Haiku_には_effort_を送らない(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    """Haiku 4.5 は effort を受け付けず API エラーになる (LLM_DETAIL_MODEL で指定された場合).

    日付付き ID でも外れないこと.
    """
    from anthropic import omit

    from recipe_system.domain.llm_output import LLMDayDetail

    client, captured = _make_client(monkeypatch, _ok_response(), model=model, effort="low")

    asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))
    assert captured["output_config"] is omit

    asyncio.run(
        client.generate(system="s", user="u", prompt_version="v1", output_schema=LLMDayDetail)
    )
    assert "effort" not in captured["output_config"]
    assert captured["output_config"]["format"]["type"] == "json_schema"

"""`recipe_system.llm.client.VertexGeminiClient` のテスト.

google-genai SDK は実際にはクライアント構築 (ネットワーク呼出なし) までは許容し、
`generate()` 内の `aio.models.generate_content` だけをモックに差し替える.
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
class _FakeUsageMetadata:
    prompt_token_count: int
    candidates_token_count: int
    cached_content_token_count: int


class _FakeResponse:
    def __init__(self, text: str | None, usage: _FakeUsageMetadata) -> None:
        self.text = text
        self.usage_metadata = usage


def _make_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> Any:
    """generate_content の呼出引数を記録しつつ response を返す VertexGeminiClient を作る."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")

    from recipe_system.llm.client import VertexGeminiClient

    client = VertexGeminiClient()

    captured: dict[str, Any] = {}

    async def _fake_generate_content(*, model: str, contents: str, config: Any) -> _FakeResponse:
        captured["model"] = model
        captured["contents"] = contents
        captured["config"] = config
        return response

    monkeypatch.setattr(client._client.aio.models, "generate_content", _fake_generate_content)
    return client, captured


def test_generate_は_system_instruction_と_response_mime_type_を渡す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(
        text='{"dishes": []}',
        usage=_FakeUsageMetadata(
            prompt_token_count=100, candidates_token_count=50, cached_content_token_count=0
        ),
    )
    client, captured = _make_client(monkeypatch, response)

    asyncio.run(client.generate(system="sys-prompt", user="user-prompt", prompt_version="v1"))

    config = captured["config"]
    assert config.system_instruction == "sys-prompt"
    assert config.response_mime_type == "application/json"
    assert captured["contents"] == "user-prompt"
    # thinking が max_output_tokens を消費して JSON が途切れるのを防ぐため無効化する
    # (実 API 検証で発覚: 2000 トークン中 1916 が thinking に消費された).
    assert config.thinking_config.thinking_budget == 0


def test_generate_は_usage_metadata_をトークン数にマッピングする(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(
        text="{}",
        usage=_FakeUsageMetadata(
            prompt_token_count=100, candidates_token_count=50, cached_content_token_count=40
        ),
    )
    client, _ = _make_client(monkeypatch, response)

    result = asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))

    assert result.input_tokens == 60  # 100 - 40 (キャッシュ分を除外)
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 40
    assert result.cache_write_tokens == 0


def test_generate_は_response_text_が_None_なら空文字(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(
        text=None,
        usage=_FakeUsageMetadata(
            prompt_token_count=10, candidates_token_count=0, cached_content_token_count=0
        ),
    )
    client, _ = _make_client(monkeypatch, response)

    result = asyncio.run(client.generate(system="s", user="u", prompt_version="v1"))

    assert result.raw_text == ""


def test_モデル解決順は_コンストラクタ引数_が最優先(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")

    from recipe_system.llm.client import VertexGeminiClient

    client = VertexGeminiClient(model="gemini-explicit")
    assert client._model == "gemini-explicit"


def test_モデル解決順は_次に_LLM_MODEL_環境変数(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")

    from recipe_system.llm.client import VertexGeminiClient

    client = VertexGeminiClient()
    assert client._model == "gemini-2.5-pro"


def test_モデル解決順は_最後に_DEFAULT_MODEL(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    from recipe_system.llm.client import VertexGeminiClient

    client = VertexGeminiClient()
    assert client._model == VertexGeminiClient.DEFAULT_MODEL

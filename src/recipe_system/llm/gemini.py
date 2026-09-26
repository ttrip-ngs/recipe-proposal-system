"""Vertex AI 経由 Gemini の実装 (google-genai SDK).

Claude のクォータが未承認の間などに実 LLM 応答を検証するための代替経路.
LLMClient Protocol を満たすので services / budget_guard からは
VertexClaudeClient と区別なく扱える.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from recipe_system.config import get_settings
from recipe_system.llm.base import LLMResponse, usage_int
from recipe_system.observability.logging import get_logger

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger(__name__)


class VertexGeminiClient:
    """Gemini 2.5 系はプロンプトの implicit caching が自動適用されるため、
    ``USE_PROMPT_CACHE`` はこのクライアントでは no-op (Claude の
    ``cache_control`` のような明示指定は不要).

    Gemini 2.5 系は既定で thinking (内部思考) が有効で、そのトークンも
    ``max_output_tokens`` の予算を消費する。本タスクは長い推論を要さない
    構造化 JSON 生成であり、既定のままだと thinking だけで予算の大半を
    使い切り可視 JSON が途中で切れる (実 API 検証で確認済み: 2000 トークン
    中 1916 トークンが thinking に消費され JSON パース失敗が発生した)。
    そのため ``thinking_budget=0`` で thinking を無効化する.
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(self, model: str | None = None) -> None:
        from google import genai

        settings = get_settings()
        self._model = model or settings.llm_model or self.DEFAULT_MODEL
        self._client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.vertex_ai_location,
        )

    async def generate(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        from google.genai import types

        started = time.perf_counter()
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                # services 側は raw_text を JSON としてパースするため、
                # markdown コードフェンス混入を避けるべく明示指定する.
                response_mime_type="application/json",
                response_schema=output_schema,
                # thinking が max_output_tokens を奪い JSON が途切れるのを防ぐ
                # (クラスの docstring 参照).
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        usage = getattr(response, "usage_metadata", None)
        cached_tokens = usage_int(usage, "cached_content_token_count")
        prompt_tokens = usage_int(usage, "prompt_token_count")
        meta = LLMResponse(
            raw_text=response.text or "",
            model=self._model,
            # Gemini の prompt_token_count はキャッシュヒット分を含むため、
            # Anthropic 流儀 (input はキャッシュ読み出し分を含まない) に揃えて差し引く.
            input_tokens=max(prompt_tokens - cached_tokens, 0),
            output_tokens=usage_int(usage, "candidates_token_count"),
            cache_read_tokens=cached_tokens,
            cache_write_tokens=0,  # implicit caching は書込課金が発生しない
            latency_ms=elapsed_ms,
        )
        logger.info(
            "vertex_gemini.generate",
            prompt_version=prompt_version,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            cache_read_tokens=meta.cache_read_tokens,
            latency_ms=meta.latency_ms,
        )
        return meta

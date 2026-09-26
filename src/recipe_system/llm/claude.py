"""Vertex AI 経由 Claude Sonnet の本実装.

anthropic[vertex] SDK を使用. Prompt Caching を `cache_control` で制御する.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from recipe_system.config import get_settings
from recipe_system.llm.anthropic_common import (
    build_output_config,
    build_system_payload,
    to_llm_response,
)
from recipe_system.llm.base import LLMResponse
from recipe_system.observability.logging import get_logger

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger(__name__)


class VertexClaudeClient:
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: str | None = None) -> None:
        from anthropic import AsyncAnthropicVertex

        settings = get_settings()
        self._model = model or settings.llm_model or self.DEFAULT_MODEL
        self._use_prompt_cache = settings.use_prompt_cache
        self._client = AsyncAnthropicVertex(
            region=settings.vertex_ai_location,
            project_id=settings.anthropic_vertex_project_id,
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
        from anthropic import omit

        started = time.perf_counter()

        system_payload = build_system_payload(system, self._use_prompt_cache)
        output_config = build_output_config(output_schema)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_payload,
            messages=[{"role": "user", "content": user}],
            output_config=output_config if output_config is not None else omit,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        meta = to_llm_response(response, fallback_model=self._model, elapsed_ms=elapsed_ms)
        logger.info(
            "vertex.generate",
            prompt_version=prompt_version,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            cache_read_tokens=meta.cache_read_tokens,
            cache_write_tokens=meta.cache_write_tokens,
            latency_ms=meta.latency_ms,
        )
        return meta

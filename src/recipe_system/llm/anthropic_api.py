"""api.anthropic.com 直接呼び出しの Claude 実装.

Vertex 経由ではなく api.anthropic.com を直接叩く経路. Vertex 側のモデル
アクセスが未承認の環境で Claude を使うために用意する
(``LLM_PROVIDER=anthropic``. Vertex 経由の ``claude`` は本番想定として残す).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from recipe_system.config import get_settings
from recipe_system.llm.anthropic_common import (
    Effort,
    build_output_config,
    build_system_payload,
    to_llm_response,
)
from recipe_system.llm.base import LLMResponse
from recipe_system.llm.pricing import normalize_model
from recipe_system.observability.logging import get_logger

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger(__name__)

# effort を受け付けないモデル (Haiku 4.5 は送るとエラー). キーは日付サフィックスなしの
# ID で、LLM_MODEL に日付付き ID を指定しても外れないよう normalize_model を通して引く.
_EFFORT_UNSUPPORTED_MODELS = frozenset({"claude-haiku-4-5"})


class AnthropicDirectClient:
    """``effort`` は用途ごとに factory が決める (骨子 medium / 食材と手順 low など).

    Opus 5.5 は thinking を無効化できず、effort が推論量の唯一の調整手段.
    None なら送らない (モデル既定: Opus 5.5 は medium).
    """

    # 献立の質を優先して Opus 5.5 を既定にする (memo/history/026: 同条件の比較で
    # Sonnet 5 は副菜・汁物が 7 日同じになる回があり、Haiku 4.5 は食材リストの精度が低い).
    DEFAULT_MODEL = "claude-opus-5-5"

    def __init__(self, model: str | None = None, *, effort: Effort | None = None) -> None:
        from anthropic import AsyncAnthropic

        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY が未設定です。"
                "LLM_PROVIDER=anthropic を使う場合は .env に設定してください。"
            )
        self._model = model or settings.llm_model or self.DEFAULT_MODEL
        unsupported = normalize_model(self._model) in _EFFORT_UNSUPPORTED_MODELS
        self._effort = None if unsupported else effort
        self._use_prompt_cache = settings.use_prompt_cache
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

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
        output_config = build_output_config(output_schema, self._effort)

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
            "anthropic.generate",
            prompt_version=prompt_version,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            cache_read_tokens=meta.cache_read_tokens,
            cache_write_tokens=meta.cache_write_tokens,
            latency_ms=meta.latency_ms,
        )
        return meta

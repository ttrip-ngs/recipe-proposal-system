"""LLM クライアント共通の型定義.

``LLMResponse`` / ``LLMClient`` はプロバイダ実装 (fake/claude/gemini) と
``budget_guard`` の双方が参照する最小単位のため、ここに切り出して循環 import
を避ける (各実装は ``llm.base`` のみに依存し、ファサードの ``llm.client`` には
依存しない).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel


@dataclass(frozen=True)
class LLMResponse:
    raw_text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0


class LLMClient(Protocol):
    """LLM 呼出の最小インタフェース.

    ``output_schema`` は応答が従うべき Pydantic モデル. 対応するプロバイダは
    API 側の構造化出力 (制約付きデコード) に使い、JSON として不正な応答
    (例: ``"quantity": 大さじ``) をそもそも生成させない. 呼出側は従来どおり
    ``parse_llm_json`` で検証するため、未対応プロバイダでも安全性は変わらない.
    """

    async def generate(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse: ...


def usage_int(usage: Any, key: str) -> int:
    """SDK の usage オブジェクト (属性 or dict) から int を安全に取り出す."""
    if usage is None:
        return 0
    value = getattr(usage, key, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

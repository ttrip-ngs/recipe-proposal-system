"""Anthropic SDK 共通処理 (Vertex 経由 / 直接 API 経由で共有する).

``claude.py`` (Vertex 経由) と ``anthropic_api.py`` (api.anthropic.com 直接)
の両方が使う system prompt caching の組み立て・レスポンス変換をここに集約する.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from recipe_system.llm.base import LLMResponse, usage_int
from recipe_system.observability.logging import get_logger

if TYPE_CHECKING:
    from anthropic.types import OutputConfigParam, TextBlockParam
    from pydantic import BaseModel

logger = get_logger(__name__)


def build_system_payload(system: str, use_prompt_cache: bool) -> list[TextBlockParam]:
    """system プロンプトを Anthropic SDK 向けの content block 配列に変換する.

    use_prompt_cache=True の場合、cache_control (ephemeral) を付与し Prompt
    Caching を有効化する.
    """
    system_block: dict[str, Any] = {"type": "text", "text": system}
    if use_prompt_cache:
        system_block["cache_control"] = {"type": "ephemeral"}
    return cast("list[TextBlockParam]", [system_block])


Effort = Literal["low", "medium", "high", "xhigh", "max"]


def build_output_config(
    output_schema: type[BaseModel] | None, effort: Effort | None = None
) -> OutputConfigParam | None:
    """``output_config`` (構造化出力スキーマ / effort) を組み立てる. 指定が無ければ None.

    スキーマは SDK の ``transform_schema`` で構造化出力が受け付ける形に変換する
    (``ge`` / ``max_length`` などの未対応制約は description に移される. 呼出側の
    ``parse_llm_json`` が Pydantic で再検証するため、制約が失われることはない).
    """
    from anthropic import transform_schema

    config: OutputConfigParam = {}
    if output_schema is not None:
        config["format"] = {"type": "json_schema", "schema": transform_schema(output_schema)}
    if effort is not None:
        config["effort"] = effort
    return config or None


def extract_text_content(response: Any) -> str:
    """Anthropic SDK の MessageBatch/Message から text を抽出する."""
    content = getattr(response, "content", None)
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def to_llm_response(response: Any, *, fallback_model: str, elapsed_ms: int) -> LLMResponse:
    """Anthropic SDK の Message レスポンスを ``LLMResponse`` に変換する.

    ``stop_reason`` が ``max_tokens`` の場合は警告ログを出す. 出力が途中で切れると
    JSON が閉じずパース失敗になるが、エラーメッセージ (「JSON デコード失敗」) からは
    原因が max_tokens 不足だと分からないため、ここで明示的に記録する.
    """
    usage = getattr(response, "usage", None)
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning(
            "anthropic.truncated",
            model=getattr(response, "model", fallback_model),
            output_tokens=usage_int(usage, "output_tokens"),
        )
    return LLMResponse(
        raw_text=extract_text_content(response),
        model=getattr(response, "model", fallback_model),
        input_tokens=usage_int(usage, "input_tokens"),
        output_tokens=usage_int(usage, "output_tokens"),
        cache_read_tokens=usage_int(usage, "cache_read_input_tokens"),
        cache_write_tokens=usage_int(usage, "cache_creation_input_tokens"),
        latency_ms=elapsed_ms,
    )

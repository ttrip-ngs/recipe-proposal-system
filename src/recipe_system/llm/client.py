"""Vertex AI 経由 LLM クライアント (Claude / Gemini) のファサード.

実体は ``llm.base`` (共通型) / ``llm.fake`` / ``llm.claude`` / ``llm.gemini``
(各プロバイダ実装) / ``llm.factory`` (組み立て) に分割されている. 本モジュールは
既存の import 経路 (``from recipe_system.llm.client import ...``) を維持する
ための re-export のみを行う.

services 層は system/user プロンプトをレンダリング済みで渡し、JSON 文字列
(LLMResponse.raw_text) を受け取る責務分担. JSON パース・ガードレール検証は
services 側で行う.

Claude が主力実装. 週間提案は骨子と日別の食材・手順 (並列) に分割しており、
詳細フェーズ用のクライアントは build_detail_client / resolve_detail_model で、
用途別の effort は effort_for で決める (docs/llm-integration.md §3.1).
Gemini はクォータ未承認時など Claude が使えない場合の代替経路として用意する
(build_client / build_raw_client 参照)。加えて Vertex 側のモデルアクセスが
未承認の環境向けに、api.anthropic.com を直接叩く経路 (AnthropicDirectClient,
LLM_PROVIDER=anthropic) も用意する。プロバイダによらずガードレール
(アレルゲン検証) はコード側で行うため、本ファイルの変更でガードレールの
安全性が変わることはない.
"""

from __future__ import annotations

# get_settings はテストが `llm_client_module.get_settings.cache_clear()` の形で
# 参照する契約があるため、facade としてこのモジュールにも束縛しておく
# (recipe_system.config の同一 lru_cache オブジェクトを指すので、どちらから
# clear しても効果は共通).
from recipe_system.config import get_settings
from recipe_system.llm.anthropic_api import AnthropicDirectClient
from recipe_system.llm.base import LLMClient, LLMResponse
from recipe_system.llm.claude import VertexClaudeClient
from recipe_system.llm.factory import (
    build_client,
    build_detail_client,
    build_raw_client,
    effort_for,
    resolve_detail_model,
)
from recipe_system.llm.fake import FakeVertexClient
from recipe_system.llm.gemini import VertexGeminiClient

__all__ = [
    "AnthropicDirectClient",
    "FakeVertexClient",
    "LLMClient",
    "LLMResponse",
    "VertexClaudeClient",
    "VertexGeminiClient",
    "build_client",
    "build_detail_client",
    "build_raw_client",
    "effort_for",
    "get_settings",
    "resolve_detail_model",
]

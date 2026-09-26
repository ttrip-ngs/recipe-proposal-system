"""LLMClient の組み立て (provider 選択 + 予算ガードでのラップ)."""

from __future__ import annotations

from typing import Literal

from recipe_system.config import get_settings
from recipe_system.llm.anthropic_api import AnthropicDirectClient
from recipe_system.llm.anthropic_common import Effort
from recipe_system.llm.base import LLMClient
from recipe_system.llm.budget_guard import BudgetGuardedClient
from recipe_system.llm.claude import VertexClaudeClient
from recipe_system.llm.fake import FakeVertexClient
from recipe_system.llm.gemini import VertexGeminiClient

Provider = Literal["fake", "claude", "gemini", "anthropic"]

Purpose = Literal["single_day", "weekly", "weekly_detail", "other"]

# 用途別の effort (anthropic プロバイダのみ有効). 献立を決める呼出 (単日・週間骨子・
# 単日修復) は medium、骨子で決まった料理の食材と手順を書くだけの詳細フェーズは low.
# 実測 (memo/history/026): Opus 5.5 の骨子は low でも破綻しないが、medium の方が
# 旬の食材・好物・週末の手間配分まで安定して反映された.
_EFFORT_BY_PURPOSE: dict[Purpose, Effort] = {
    "single_day": "medium",
    "weekly": "medium",
    "weekly_detail": "low",
    "other": "medium",
}


def effort_for(purpose: Purpose) -> Effort:
    """用途に対応する effort を返す (評価ランナーも本番と同じ値を使うために公開する)."""
    return _EFFORT_BY_PURPOSE[purpose]


def resolve_detail_model() -> str | None:
    """詳細フェーズに使うモデル名を返す (None なら骨子と同じ既定モデル).

    既定では骨子と同じモデルを使う (Haiku 4.5 は食材リストの精度が足りなかった.
    memo/history/026). ``LLM_DETAIL_MODEL`` で上書きできる.
    """
    return get_settings().llm_detail_model


def build_raw_client(
    provider: Provider | None = None,
    *,
    model: str | None = None,
    effort: Effort | None = None,
) -> LLMClient:
    """``BudgetGuardedClient`` で包む前の生クライアントを provider から組み立てる.

    provider 省略時は ``settings.effective_llm_provider`` に従う.
    evaluation/runner.py など、予算ガード・Firestore 記録の外で LLM を
    直接叩きたい経路のための入口 (build_client は常にこちらを内部で使う).

    ``model`` を渡すと各クライアントの既定モデル・``LLM_MODEL`` の双方より
    優先される. ``effort`` は anthropic プロバイダのみ有効 (他は推論量の指定を持たない).
    """
    settings = get_settings()
    resolved = provider or settings.effective_llm_provider
    if resolved == "fake":
        return FakeVertexClient()
    if resolved == "gemini":
        return VertexGeminiClient(model)
    if resolved == "anthropic":
        return AnthropicDirectClient(model, effort=effort)
    return VertexClaudeClient(model)


def build_client(
    *,
    purpose: Purpose,
    family_id: str,
    model: str | None = None,
) -> LLMClient:
    """LLMClient を組み立てる. ``BudgetGuardedClient`` で常にラップする.

    purpose / family_id は ``llm_usage`` コレクションへの記録と予算チェックに使う.
    Fake / 実 Vertex (Claude・Gemini) のいずれでも記録経路は同一にして、
    ローカル開発でも ``/admin/usage`` の動作確認ができるようにする (Fake は cost=0).
    """
    # get_firestore_client はテストで module 属性を monkeypatch されるため、
    # 呼出のたびに import して現在の属性値を解決する (トップレベル import だと
    # patch 前の関数オブジェクトに固定されてしまう).
    from recipe_system.repository.firestore_client import get_firestore_client

    settings = get_settings()
    return BudgetGuardedClient(
        build_raw_client(model=model, effort=effort_for(purpose)),
        purpose=purpose,
        family_id=family_id,
        firestore_client=get_firestore_client(),
        usd_jpy_rate=settings.usd_jpy_rate,
    )


def build_detail_client(*, family_id: str) -> LLMClient:
    """週間提案の詳細フェーズ (食材リスト + 手順の生成) 用クライアントを組み立てる.

    骨子フェーズと effort・コストの性質が違うため ``purpose="weekly_detail"`` で記録し、
    ``/admin/usage`` で按分が見えるようにする.

    既知の挙動: 詳細フェーズは 7 日分を並列に呼ぶため、予算上限の際では各呼出の
    preflight が同じ累積値を見て通過し、最大 7 呼出分だけ上限を超過しうる.
    既存の並列修復と同じ性質で、詳細 1 回は数円程度のため許容している.
    """
    return build_client(
        purpose="weekly_detail",
        family_id=family_id,
        model=resolve_detail_model(),
    )

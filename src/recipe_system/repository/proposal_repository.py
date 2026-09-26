"""proposals コレクションのアクセス層.

LLM 提案ログはプロンプト改善・監査・評価の入力として残すため、全件保存する.
リポジトリは services 層に依存させない (dict ベースの IF で受ける).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore


def save_proposal_record(
    client: firestore.Client,
    *,
    family_id: str,
    record: dict[str, Any],
    requested_at: datetime | None = None,
) -> str:
    """提案ログを 1 件書き込み、ドキュメント ID を返す.

    record は to_firestore_record() が返す dict をそのまま受ける想定.
    """
    doc_ref = client.collection("proposals").document()
    payload = {
        "family_id": family_id,
        "requested_at": requested_at or datetime.now(UTC),
        **record,
    }
    doc_ref.set(payload)
    return doc_ref.id


def update_proposal_record(
    client: firestore.Client,
    proposal_id: str,
    record: dict[str, Any],
) -> None:
    """既存の提案ドキュメントに record を merge する (pending -> ready 遷移等)."""
    client.collection("proposals").document(proposal_id).update(record)


def get_proposal(client: firestore.Client, proposal_id: str) -> dict[str, Any] | None:
    doc = client.collection("proposals").document(proposal_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def mark_accepted(client: firestore.Client, proposal_id: str, accepted: bool) -> None:
    client.collection("proposals").document(proposal_id).update({"accepted": accepted})


def pending_placeholder_record() -> dict[str, Any]:
    """pending 状態のプレースホルダ. BackgroundTask 完了時に update_proposal_record で差し替える.

    ``error_code`` は予算超過時 (``budget_exceeded``) などの UI 分岐用の機械可読フィールド.
    通常成功時は None のまま残る.
    """
    return {
        "status": "pending",
        "prompt_version": "pending",
        "model": "pending",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "latency_ms": 0,
        "dishes": [],
        "violations": [],
        "retry_count": 0,
        "overall_comment": None,
        "succeeded": False,
        "accepted": None,
        "error_code": None,
    }

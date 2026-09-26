"""`config/llm_budget` ドキュメントの読み書き.

予算上限値はランタイム編集できる必要があるため (Cloud Run 再デプロイ不要),
env ではなく Firestore に保存する.

Firestore 取得失敗時は block-to-safe の方針: 呼出側で例外を伝搬し
``BudgetExceededError`` に変換する (`llm/budget_guard.py` 参照).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from google.cloud import firestore

CONFIG_COLLECTION = "config"
BUDGET_DOC_ID = "llm_budget"
DEFAULT_MONTHLY_JPY = 3000.0
DEFAULT_WARN_THRESHOLD_PCT = 80
# 予算消化率の段階閾値. 100% で danger に切替えるためのモジュール定数.
BUDGET_FULL_PCT = 100.0

BudgetTone = Literal["ok", "warn", "danger"]


@dataclass(frozen=True)
class BudgetConfig:
    monthly_jpy_limit: float
    warn_threshold_pct: int


def get_budget_config(client: firestore.Client) -> BudgetConfig:
    """予算ドキュメントを 1 回読んで月次予算・警告閾値をまとめて返す.

    ``get_budget_jpy`` / ``get_warn_threshold_pct`` を両方呼ぶと同一ドキュメントを
    2 回読むことになるため、両方が必要な呼び出し元はこちらを使う.
    ドキュメントが存在しない場合はデフォルト値を返す.
    Firestore 通信エラーは捕捉せず例外を上位に伝搬させる.
    """
    doc = client.collection(CONFIG_COLLECTION).document(BUDGET_DOC_ID).get()
    if not doc.exists:
        return BudgetConfig(DEFAULT_MONTHLY_JPY, DEFAULT_WARN_THRESHOLD_PCT)
    data = doc.to_dict() or {}
    return BudgetConfig(
        monthly_jpy_limit=_coerce(data.get("monthly_jpy_limit"), float, DEFAULT_MONTHLY_JPY),
        warn_threshold_pct=_coerce(data.get("warn_threshold_pct"), int, DEFAULT_WARN_THRESHOLD_PCT),
    )


def _coerce[T](value: object, cast: type[T], default: T) -> T:
    if value is None:
        return default
    try:
        return cast(value)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return default


def get_budget_jpy(client: firestore.Client) -> float:
    """月次予算 (JPY) を取得する.

    ``get_warn_threshold_pct`` と両方必要な場合は ``get_budget_config`` を使うこと
    (ドキュメント読み取りが重複しない).
    """
    return get_budget_config(client).monthly_jpy_limit


def get_warn_threshold_pct(client: firestore.Client) -> int:
    """予算警告閾値 (パーセント) を取得する. デフォルト 80.

    ``get_budget_jpy`` と両方必要な場合は ``get_budget_config`` を使うこと
    (ドキュメント読み取りが重複しない).
    """
    return get_budget_config(client).warn_threshold_pct


def set_budget_jpy(client: firestore.Client, jpy: float) -> None:
    """月次予算を更新する (運用スクリプト用)."""
    client.collection(CONFIG_COLLECTION).document(BUDGET_DOC_ID).set(
        {
            "monthly_jpy_limit": float(jpy),
            "updated_at": datetime.now(UTC),
        },
        merge=True,
    )


def derive_budget_status(
    *, current_jpy: float, budget_jpy: float, warn_pct: int
) -> tuple[float, BudgetTone]:
    """累積コスト・予算・警告閾値から usage_pct と表示 tone を導出する.

    admin ページとダッシュボードのウィジェットで同じ判定が必要なため共通化.
    """
    usage_pct = (current_jpy / budget_jpy * 100.0) if budget_jpy > 0 else 0.0
    if usage_pct >= BUDGET_FULL_PCT:
        return usage_pct, "danger"
    if usage_pct >= warn_pct:
        return usage_pct, "warn"
    return usage_pct, "ok"

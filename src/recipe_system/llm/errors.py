"""LLM 呼出周りの例外型.

`BudgetExceededError` は予算超過時に `BudgetGuardedClient` から投げられ、
`web/routes/plans.py` の BackgroundTask で捕捉して UI 表示に変換する.
"""

from __future__ import annotations


class BudgetExceededError(Exception):
    """月次 LLM 予算を超過した場合に投げる例外.

    予算照会失敗 (Firestore エラー) も block-to-safe として同じ例外で表現する.
    呼出側 (BackgroundTask) は `user_message` をそのまま UI に出すか
    `error_message` フィールドに格納する.
    """

    user_message: str = (
        "今月の AI 利用予算を超過しました。Firestore の config/llm_budget で上限を上げてください。"
    )

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(detail or self.user_message)

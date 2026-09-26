"""LLM 利用ログのドメインモデル.

`llm_usage` Firestore コレクションに 1 呼出 1 ドキュメントとして保存される.
``year_month`` は ``where("year_month","==","2026-05")`` で月次集計するための
非正規化フィールド (家庭利用規模では index 不要).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# weekly_detail は週間提案の分割経路 (骨子 Sonnet / 詳細 Haiku) における詳細フェーズ.
# 骨子と詳細でモデル・単価が違うため purpose を分けて按分を見えるようにする.
UsagePurpose = Literal["single_day", "weekly", "weekly_detail", "other"]


class LLMUsageRecord(BaseModel):
    """LLM 呼出 1 件分の利用記録.

    success=False かつ tokens=0 の場合は preflight 段階での失敗ではなく、
    実際に API を呼んで例外が出たケース (パースエラー等) を表す.
    Preflight ブロックは API を呼んでいないため記録しない.
    """

    timestamp: datetime
    family_id: str
    purpose: UsagePurpose
    model: str
    year_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_jpy: float = 0.0
    success: bool = True
    retry_attempt: int = 0
    error_code: str | None = None

    def to_firestore_dict(self) -> dict[str, object]:
        """Firestore set() に渡す dict 表現."""
        return {
            "timestamp": self.timestamp,
            "family_id": self.family_id,
            "purpose": self.purpose,
            "model": self.model,
            "year_month": self.year_month,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "latency_ms": self.latency_ms,
            "cost_jpy": self.cost_jpy,
            "success": self.success,
            "retry_attempt": self.retry_attempt,
            "error_code": self.error_code,
        }


def year_month_of(dt: datetime | date) -> str:
    """datetime / date を ``YYYY-MM`` 形式の文字列に変換する."""
    return f"{dt.year:04d}-{dt.month:02d}"

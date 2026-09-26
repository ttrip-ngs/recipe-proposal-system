"""LLM 呼出の予算ガード.

`build_client` から ``LLMClient`` Protocol を満たすラッパとして返り、
内側の ``FakeVertexClient`` / ``VertexClaudeClient`` を以下で取り囲む.

1. **Preflight**: 月次累積コスト + 予算上限を Firestore から取得し、
   累積が上限以上なら API を呼ばずに ``BudgetExceededError`` を投げる.
   Firestore 取得失敗時も block-to-safe で同じ例外を投げる
   (家庭の月数千円の予算を守ることが目的なので fail-open しない).

2. **記録**: ``inner.generate`` を呼んだ後 (例外発生時含む) に
   ``llm_usage`` コレクションへ 1 件 append する. Preflight ブロック時は
   API を呼んでいないため記録しない.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from recipe_system.domain.llm_usage import LLMUsageRecord, UsagePurpose, year_month_of
from recipe_system.llm.base import LLMResponse
from recipe_system.llm.errors import BudgetExceededError
from recipe_system.llm.pricing import calculate_cost_jpy
from recipe_system.observability.logging import get_logger
from recipe_system.repository.llm_budget_repository import get_budget_jpy
from recipe_system.repository.llm_usage_repository import append_usage, monthly_total_jpy

if TYPE_CHECKING:
    from google.cloud import firestore
    from pydantic import BaseModel

    from recipe_system.llm.base import LLMClient

logger = get_logger(__name__)


class BudgetGuardedClient:
    """LLMClient Protocol を満たすラッパ. 予算チェックと利用記録を担う."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        purpose: UsagePurpose,
        family_id: str,
        firestore_client: firestore.Client,
        usd_jpy_rate: float,
    ) -> None:
        self._inner = inner
        self._purpose = purpose
        self._family_id = family_id
        self._fs = firestore_client
        self._usd_jpy_rate = usd_jpy_rate

    async def generate(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        now = datetime.now(UTC)
        ym = year_month_of(now)

        # 1) Preflight: block-to-safe.
        try:
            budget = get_budget_jpy(self._fs)
            current = monthly_total_jpy(self._fs, family_id=self._family_id, year_month=ym)
        except Exception as e:
            logger.warning(
                "budget_guard.preflight_failed",
                family_id=self._family_id,
                year_month=ym,
                error=str(e),
            )
            raise BudgetExceededError(f"予算照会に失敗しました: {e}") from e

        if current >= budget:
            logger.warning(
                "budget_guard.blocked",
                family_id=self._family_id,
                year_month=ym,
                current_jpy=current,
                budget_jpy=budget,
            )
            raise BudgetExceededError(
                f"今月の累積コスト ¥{current:.1f} が上限 ¥{budget:.1f} に達しています"
            )

        # 2) API 呼出 + finally で記録.
        response: LLMResponse | None = None
        error_code: str | None = None
        try:
            response = await self._inner.generate(
                system=system,
                user=user,
                prompt_version=prompt_version,
                max_tokens=max_tokens,
                output_schema=output_schema,
            )
        except Exception as e:
            error_code = type(e).__name__
            raise
        else:
            return response
        finally:
            self._record_usage(response=response, error_code=error_code, now=now, ym=ym)

    def _record_usage(
        self,
        *,
        response: LLMResponse | None,
        error_code: str | None,
        now: datetime,
        ym: str,
    ) -> None:
        """成功・失敗を問わず 1 件記録する.

        記録系の失敗 (Pydantic バリデーション・Firestore 書込) で
        本処理 (LLM 応答) を巻き込まないよう、構築・書込の双方を例外捕捉する.
        """
        try:
            if response is not None:
                cost = calculate_cost_jpy(
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cache_read_tokens=response.cache_read_tokens,
                    cache_write_tokens=response.cache_write_tokens,
                    usd_jpy_rate=self._usd_jpy_rate,
                )
                record = LLMUsageRecord(
                    timestamp=now,
                    family_id=self._family_id,
                    purpose=self._purpose,
                    model=response.model,
                    year_month=ym,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cache_read_tokens=response.cache_read_tokens,
                    cache_write_tokens=response.cache_write_tokens,
                    latency_ms=response.latency_ms,
                    cost_jpy=cost,
                    success=True,
                    error_code=None,
                )
            else:
                # API 呼出中に例外が出たケース. トークンは確定値が取れないため 0.
                record = LLMUsageRecord(
                    timestamp=now,
                    family_id=self._family_id,
                    purpose=self._purpose,
                    model="unknown",
                    year_month=ym,
                    success=False,
                    error_code=error_code,
                )
            append_usage(self._fs, record)
        except Exception as e:
            logger.warning(
                "budget_guard.record_failed",
                family_id=self._family_id,
                purpose=self._purpose,
                error=str(e),
            )

"""`llm_usage_repository._to_record` の purpose 復元のテスト.

Firestore から読み戻すときの purpose 白リストは `UsagePurpose` から導出している.
ここに白リストを直書きすると Literal を拡張したときに読み出し側だけ取り残され、
正しく書けた記録が静かに "other" に落ちる (weekly_detail 追加時に実際に起きた).
エミュレータ不要の純粋関数なのでユニットテストで固定する.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from recipe_system.domain.llm_usage import UsagePurpose
from recipe_system.repository.llm_usage_repository import _to_record


def _data(purpose: str) -> dict[str, object]:
    return {
        "timestamp": datetime(2026, 8, 1, tzinfo=UTC),
        "family_id": "family-test",
        "purpose": purpose,
        "model": "claude-haiku-4-5",
        "year_month": "2026-08",
    }


@pytest.mark.parametrize("purpose", get_args(UsagePurpose))
def test_UsagePurpose_の全ての値がそのまま復元される(purpose: str) -> None:
    assert _to_record(_data(purpose)).purpose == purpose


def test_未知のpurposeはotherに落とす() -> None:
    assert _to_record(_data("unknown_purpose")).purpose == "other"

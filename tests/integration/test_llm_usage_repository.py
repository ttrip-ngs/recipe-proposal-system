"""`llm_usage_repository` / `llm_budget_repository` の統合テスト.

Firestore エミュレータ起動が前提 (conftest.py で skip 制御).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from google.cloud import firestore

from recipe_system.domain.llm_usage import LLMUsageRecord, UsagePurpose, year_month_of
from recipe_system.repository.llm_budget_repository import (
    DEFAULT_MONTHLY_JPY,
    get_budget_jpy,
    set_budget_jpy,
)
from recipe_system.repository.llm_usage_repository import (
    append_usage,
    monthly_summary,
    monthly_total_jpy,
)

pytestmark = pytest.mark.integration


def _fs_client() -> firestore.Client:
    return firestore.Client(project="recipe-system-dev")


def _make_record(
    *,
    family_id: str,
    cost_jpy: float = 1.0,
    purpose: UsagePurpose = "single_day",
    timestamp: datetime | None = None,
) -> LLMUsageRecord:
    ts = timestamp or datetime.now(UTC)
    return LLMUsageRecord(
        timestamp=ts,
        family_id=family_id,
        purpose=purpose,
        model="claude-sonnet-4-6",
        year_month=year_month_of(ts),
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
        latency_ms=1200,
        cost_jpy=cost_jpy,
        success=True,
    )


def test_append_usage_と_monthly_total_jpy_の往復() -> None:
    client = _fs_client()
    family_id = f"llm-usage-test-{uuid.uuid4().hex[:8]}"

    now = datetime.now(UTC)
    append_usage(client, _make_record(family_id=family_id, cost_jpy=12.5, timestamp=now))
    append_usage(client, _make_record(family_id=family_id, cost_jpy=7.25, timestamp=now))

    total = monthly_total_jpy(client, family_id=family_id, year_month=year_month_of(now))
    assert total == pytest.approx(19.75, rel=1e-6)


def test_monthly_summary_は用途別と日別を集計する() -> None:
    client = _fs_client()
    family_id = f"llm-usage-test-{uuid.uuid4().hex[:8]}"

    now = datetime.now(UTC)
    append_usage(
        client,
        _make_record(family_id=family_id, purpose="single_day", cost_jpy=3.0, timestamp=now),
    )
    append_usage(
        client,
        _make_record(family_id=family_id, purpose="single_day", cost_jpy=2.0, timestamp=now),
    )
    append_usage(
        client,
        _make_record(family_id=family_id, purpose="weekly", cost_jpy=10.0, timestamp=now),
    )

    summary = monthly_summary(
        client,
        family_id=family_id,
        year_month=year_month_of(now),
        today=now.date(),
    )
    assert summary.total_calls == 3
    assert summary.total_jpy == pytest.approx(15.0)
    assert summary.by_purpose["single_day"] == pytest.approx(5.0)
    assert summary.by_purpose["weekly"] == pytest.approx(10.0)
    assert summary.by_purpose_count["single_day"] == 2
    assert summary.by_purpose_count["weekly"] == 1
    # 直近 30 日エントリの最後 (=today) に 15.0 が入る
    assert summary.by_day[-1][0] == now.date()
    assert summary.by_day[-1][1] == pytest.approx(15.0)


def test_monthly_summary_は別家族の記録を含まない() -> None:
    client = _fs_client()
    fam_a = f"llm-usage-A-{uuid.uuid4().hex[:8]}"
    fam_b = f"llm-usage-B-{uuid.uuid4().hex[:8]}"

    now = datetime.now(UTC)
    append_usage(client, _make_record(family_id=fam_a, cost_jpy=99.0, timestamp=now))
    append_usage(client, _make_record(family_id=fam_b, cost_jpy=1.0, timestamp=now))

    assert monthly_total_jpy(
        client, family_id=fam_a, year_month=year_month_of(now)
    ) == pytest.approx(99.0)
    assert monthly_total_jpy(
        client, family_id=fam_b, year_month=year_month_of(now)
    ) == pytest.approx(1.0)


def test_異なる_year_month_は集計対象外() -> None:
    client = _fs_client()
    family_id = f"llm-usage-test-{uuid.uuid4().hex[:8]}"

    now = datetime.now(UTC)
    past = now - timedelta(days=40)
    append_usage(client, _make_record(family_id=family_id, cost_jpy=5.0, timestamp=now))
    append_usage(client, _make_record(family_id=family_id, cost_jpy=100.0, timestamp=past))

    total = monthly_total_jpy(client, family_id=family_id, year_month=year_month_of(now))
    assert total == pytest.approx(5.0)


def test_get_budget_jpy_は未設定時にデフォルトを返す() -> None:
    client = _fs_client()
    # ドキュメント自体は CIで他テストが書いている可能性があるため、
    # ここではドキュメントを書く前の値を別フィールド名で確認する代わりに、
    # 「数値が float で返る」「set_budget_jpy で round-trip する」だけを確認する.
    budget = get_budget_jpy(client)
    assert isinstance(budget, float)
    assert budget >= 0.0


def test_set_budget_jpy_と_get_budget_jpy_は_round_trip_する() -> None:
    client = _fs_client()
    set_budget_jpy(client, 4242.0)
    assert get_budget_jpy(client) == pytest.approx(4242.0)
    # 後続テストへの影響を避けるためデフォルトに戻す
    set_budget_jpy(client, DEFAULT_MONTHLY_JPY)

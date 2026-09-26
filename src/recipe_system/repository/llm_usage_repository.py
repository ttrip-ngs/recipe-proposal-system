"""`llm_usage` Firestore コレクションへの永続化と月次集計.

家庭利用規模 (~60 件/月) を前提とし、月次集計はクエリ on-demand で実施する.
規模が大きくなれば ``monthly_summary/{YYYY-MM}`` ドキュメントへの事前集計に
切替えるが、その時点で初めて検討すれば良い (YAGNI).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast, get_args

from google.cloud import firestore

from recipe_system.domain.llm_usage import LLMUsageRecord, UsagePurpose

COLLECTION = "llm_usage"


@dataclass
class MonthlySummary:
    """``/admin/usage`` と ダッシュボードウィジェットで使う月次サマリ.

    by_day は今月分だけでなく直近 30 日 (今日含む遡り) を埋めて返す.
    呼出件数が 0 の日も 0 円のエントリで埋めるためグラフで欠落しない.
    """

    year_month: str
    total_jpy: float = 0.0
    total_calls: int = 0
    success_calls: int = 0
    by_purpose: dict[str, float] = field(default_factory=dict)
    by_purpose_count: dict[str, int] = field(default_factory=dict)
    by_day: list[tuple[date, float]] = field(default_factory=list)
    cache_hit_rate: float = 0.0
    recent_records: list[LLMUsageRecord] = field(default_factory=list)


def append_usage(client: firestore.Client, record: LLMUsageRecord) -> None:
    """利用記録を 1 件追加する.

    ID は Firestore 自動採番. timestamp は UTC を前提とする
    (検索クエリは ``year_month`` で済むので timestamp index は不要).
    """
    client.collection(COLLECTION).add(record.to_firestore_dict())


def _to_record(data: dict[str, Any]) -> LLMUsageRecord:
    """Firestore dict から LLMUsageRecord を組み立てる.

    Firestore SDK の戻り値は実質 Any (フィールド型は動的) のためここで型を整える.
    timestamp は SDK が tz-aware で返すが安全のためフォールバックも置く.
    """
    timestamp = data.get("timestamp")
    if not isinstance(timestamp, datetime):
        timestamp = datetime.now(UTC)
    purpose_raw = data.get("purpose")
    # 許容値は UsagePurpose から導出する. ここに白リストを直書きすると Literal を
    # 拡張したときに読み出し側だけ取り残され、正しく書けた記録が静かに "other" へ
    # 落ちる (weekly_detail 追加時に実際に起きた).
    purpose: UsagePurpose = (
        cast(UsagePurpose, purpose_raw) if purpose_raw in get_args(UsagePurpose) else "other"
    )
    error_code_raw = data.get("error_code")
    return LLMUsageRecord(
        timestamp=timestamp,
        family_id=str(data.get("family_id", "")),
        purpose=purpose,
        model=str(data.get("model", "")),
        year_month=str(data.get("year_month", "")),
        input_tokens=int(data.get("input_tokens") or 0),
        output_tokens=int(data.get("output_tokens") or 0),
        cache_read_tokens=int(data.get("cache_read_tokens") or 0),
        cache_write_tokens=int(data.get("cache_write_tokens") or 0),
        latency_ms=int(data.get("latency_ms") or 0),
        cost_jpy=float(data.get("cost_jpy") or 0.0),
        success=bool(data.get("success", True)),
        retry_attempt=int(data.get("retry_attempt") or 0),
        error_code=error_code_raw if isinstance(error_code_raw, str) else None,
    )


def _query_records(
    client: firestore.Client, *, family_id: str, year_month: str
) -> list[LLMUsageRecord]:
    """家族 + 月で絞り込んだ生レコードを取得する.

    Firestore の where は単一フィールド等値 2 つの組み合わせなので index 不要.
    """
    docs = (
        client.collection(COLLECTION)
        .where(filter=firestore.FieldFilter("family_id", "==", family_id))
        .where(filter=firestore.FieldFilter("year_month", "==", year_month))
        .stream()
    )
    return [_to_record(d.to_dict() or {}) for d in docs]


def monthly_total_jpy(client: firestore.Client, *, family_id: str, year_month: str) -> float:
    """月次累積コストのみを返す軽量版 (preflight チェック用).

    monthly_summary() より速いわけではないが意図が明確になる.
    """
    records = _query_records(client, family_id=family_id, year_month=year_month)
    return sum(r.cost_jpy for r in records)


def monthly_summary(
    client: firestore.Client,
    *,
    family_id: str,
    year_month: str,
    today: date | None = None,
) -> MonthlySummary:
    """月次サマリをまとめて返す (admin ページ用).

    ``today`` を渡すとそれを基準に直近 30 日の日別エントリを埋める.
    省略時は UTC の今日.
    """
    records = _query_records(client, family_id=family_id, year_month=year_month)
    today = today or datetime.now(UTC).date()

    total_jpy = sum(r.cost_jpy for r in records)
    success_calls = sum(1 for r in records if r.success)

    by_purpose: dict[str, float] = defaultdict(float)
    by_purpose_count: dict[str, int] = defaultdict(int)
    by_day_jpy: dict[date, float] = defaultdict(float)
    cache_read_sum = 0
    input_sum = 0

    for r in records:
        by_purpose[r.purpose] += r.cost_jpy
        by_purpose_count[r.purpose] += 1
        by_day_jpy[r.timestamp.date()] += r.cost_jpy
        cache_read_sum += r.cache_read_tokens
        input_sum += r.input_tokens

    day_window = [today - timedelta(days=i) for i in range(29, -1, -1)]
    by_day = [(d, by_day_jpy.get(d, 0.0)) for d in day_window]

    cache_total = cache_read_sum + input_sum
    cache_hit_rate = (cache_read_sum / cache_total) if cache_total > 0 else 0.0

    recent = sorted(records, key=lambda r: r.timestamp, reverse=True)[:20]

    return MonthlySummary(
        year_month=year_month,
        total_jpy=total_jpy,
        total_calls=len(records),
        success_calls=success_calls,
        by_purpose=dict(by_purpose),
        by_purpose_count=dict(by_purpose_count),
        by_day=by_day,
        cache_hit_rate=cache_hit_rate,
        recent_records=recent,
    )

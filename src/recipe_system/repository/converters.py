"""Firestore 値の date/datetime 変換共通ヘルパー.

Firestore は datetime を native Timestamp で返すが、テストやスクリプトからの
書き込み経路では ISO 文字列が入っていることもあるため、両方を許容する.
各 repository (meal_plan / shopping_list / family / history) が個別に持って
いた同一ロジックをここに集約する.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def to_date(value: object) -> date:
    """date 値への変換 (date/datetime/ISO文字列を許容). 変換不能なら ValueError."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"date に変換できません: {value!r}")


def to_datetime_or_none(value: object) -> datetime | None:
    """datetime への変換 (datetime/ISO文字列を許容). None/未知の型なら None を返す."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def to_datetime(value: object) -> datetime:
    """datetime への変換 (datetime/ISO文字列を許容). 変換不能なら現在時刻 (UTC) を返す."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return datetime.now(UTC)

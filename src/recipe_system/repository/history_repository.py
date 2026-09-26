"""history コレクションのアクセス層."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from recipe_system.repository.converters import to_datetime


def recent_history(
    client: firestore.Client,
    family_id: str,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> tuple[tuple[str, datetime], ...]:
    """(recipe_name, cooked_at) のタプル列を直近 days 日ぶん返す."""
    current = now or datetime.now(UTC)
    threshold = current - timedelta(days=days)
    query = (
        client.collection("history")
        .where(filter=FieldFilter("family_id", "==", family_id))
        .where(filter=FieldFilter("cooked_at", ">=", threshold))
        .order_by("cooked_at", direction=firestore.Query.DESCENDING)
    )
    result: list[tuple[str, datetime]] = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        name = data.get("recipe_name") or data.get("recipe_id") or ""
        cooked_at = to_datetime(data.get("cooked_at"))
        if not name:
            continue
        result.append((name, cooked_at))
    return tuple(result)

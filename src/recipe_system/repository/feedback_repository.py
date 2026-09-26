"""feedback コレクションのアクセス層."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from google.cloud import firestore

Rating = Literal["good", "neutral", "pass"]


def save_feedback(
    client: firestore.Client,
    *,
    family_id: str,
    proposal_id: str,
    rating: Rating,
    comment: str | None = None,
    submitted_at: datetime | None = None,
) -> str:
    doc_ref = client.collection("feedback").document()
    doc_ref.set(
        {
            "family_id": family_id,
            "proposal_id": proposal_id,
            "rating": rating,
            "comment": comment,
            "submitted_at": submitted_at or datetime.now(UTC),
        }
    )
    return doc_ref.id

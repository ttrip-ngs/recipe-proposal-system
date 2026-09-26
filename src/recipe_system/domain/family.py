"""家族プロファイル ドメインモデル."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FamilyMember(BaseModel):
    member_id: str
    name: str
    role: str | None = None
    allergens: frozenset[str] = Field(default_factory=frozenset)
    dislikes: frozenset[str] = Field(default_factory=frozenset)
    likes: frozenset[str] = Field(default_factory=frozenset)
    notes: str | None = None
    reviewed_at: datetime


class FamilyProfile(BaseModel):
    family_id: str
    name: str
    timezone: str = "Asia/Tokyo"
    allowed_emails: frozenset[str] = Field(default_factory=frozenset)
    members: tuple[FamilyMember, ...] = ()
    created_at: datetime
    updated_at: datetime

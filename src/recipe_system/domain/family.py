"""家族プロファイル ドメインモデル."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 通常は除去不要な食品 (醤油など) をそのメンバーが摂取してよいか. 未設定は block 扱い (ADR 0007)
ItemPolicy = Literal["allow", "block"]


class FamilyMember(BaseModel):
    member_id: str
    name: str
    role: str | None = None
    allergens: frozenset[str] = Field(default_factory=frozenset)
    # アレルギー指定 -> {食品 canonical -> allow/block}. 例: {"大豆": {"醤油": "allow"}}
    item_policies: dict[str, dict[str, ItemPolicy]] = Field(default_factory=dict)
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

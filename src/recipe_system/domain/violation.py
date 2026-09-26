"""ガード違反 ドメインモデル."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["block", "warn"]


class Violation(BaseModel):
    severity: Severity
    member: str = Field(..., description="違反が発生した家族メンバー名")
    ingredient: str = Field(..., description="違反を発生させた食材名 (生のまま)")
    canonical: str = Field(..., description="食材の canonical 名")
    reason: str

    model_config = {"frozen": True}

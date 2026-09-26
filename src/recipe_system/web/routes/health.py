"""ヘルスチェック."""

from __future__ import annotations

from fastapi import APIRouter

from recipe_system import __version__

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}

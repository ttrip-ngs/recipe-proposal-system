"""ルーター間で重複していた日付パース・週開始日検証の共通ヘルパー.

``plans.py`` / ``shopping.py`` / ``calendar.py`` にそれぞれ個別実装があった
``_parse_date`` と、``plans.py`` 内 3 箇所 + ``shopping.py`` の
``_require_monday`` を統合する.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status

from recipe_system.services.calendar_view import monday_of


def parse_date(raw: str) -> date:
    """YYYY-MM-DD 形式の日付文字列をパースする (省略不可. パスパラメータ用)."""
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"日付は YYYY-MM-DD 形式で指定してください: {raw!r}",
        ) from e


def parse_date_or_default(raw: str | None, fallback: date) -> date:
    """クエリパラメータの日付をパースする. 未指定 (空文字/None) 時は fallback を返す."""
    if not raw:
        return fallback
    return parse_date(raw)


def require_monday(d: date) -> date:
    """``d`` が週の月曜日であることを検証する. 週単位 API の入力チェック用."""
    if d != monday_of(d):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"week_start は月曜日を指定してください (例: {monday_of(d).isoformat()})。",
        )
    return d

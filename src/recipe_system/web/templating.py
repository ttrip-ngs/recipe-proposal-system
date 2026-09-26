"""Jinja2 テンプレート設定と静的ファイルディレクトリ定数."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# MealPlanStatus (domain) の日本語表示ラベル. plans/day.html・
# partials/calendar_cell.html・partials/_macros.html (status_badge) にそれぞれ
# 個別定義されていた同一辞書をここに一元化し、Jinja グローバルとして公開する.
STATUS_LABELS: dict[str, str] = {
    "empty": "未定",
    "proposed": "提案中",
    "confirmed": "確定",
    "cooked": "調理済",
    "skipped": "スキップ",
}


# ヘッダーナビ (layout.html) と bottom_nav マクロ (partials/_macros.html) が
# 個別に同じ 5 リンクを定義していたため、リンク先とラベルをここに一元化する.
# アイコン SVG は表示詳細のため bottom_nav マクロ側に残す (key で対応付け).
NAV_ITEMS: tuple[dict[str, str], ...] = (
    {"key": "today", "url": "/", "header_label": "今日", "bottom_label": "今日"},
    {"key": "week", "url": "/calendar/week", "header_label": "カレンダー", "bottom_label": "週"},
    {
        "key": "shopping",
        "url": "/shopping/week",
        "header_label": "買い物",
        "bottom_label": "買い物",
    },
    {"key": "family", "url": "/profile", "header_label": "家族", "bottom_label": "家族"},
    {"key": "menu", "url": "/history", "header_label": "記録", "bottom_label": "記録"},
)


def static_url(path: str) -> str:
    """静的ファイルの URL にファイル更新時刻をクエリとして付ける.

    ブラウザは /static 配下を再検証なしにキャッシュすることがあり、CSS/JS を
    更新しても古い内容が使われ続ける (実際に app.js の更新が反映されず、
    JS 由来の機能が動かない事象が発生した)。mtime をクエリに含めることで、
    内容が変わったときだけ URL が変わり確実に再取得される。
    ファイルが無い場合はクエリなしで返す (テスト等で static を差し替えた場合)。
    """
    target = STATIC_DIR / path.lstrip("/")
    try:
        stamp = int(target.stat().st_mtime)
    except OSError:
        return f"/static/{path.lstrip('/')}"
    return f"/static/{path.lstrip('/')}?v={stamp}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["status_labels"] = STATUS_LABELS
templates.env.globals["nav_items"] = NAV_ITEMS
templates.env.globals["static_url"] = static_url

"""services.calendar_view のユニットテスト."""

from __future__ import annotations

from datetime import UTC, date, datetime

from recipe_system.domain import MealPlan, MealPlanDish
from recipe_system.services.calendar_view import (
    JST,
    build_month_view,
    build_week_view,
    monday_of,
    month_range_dates,
    today_in_jst,
    week_range,
)


def _plan(d: date, status: str = "confirmed", dish: str = "肉じゃが") -> MealPlan:
    return MealPlan(
        family_id="fam",
        plan_date=d,
        status=status,  # type: ignore[arg-type]
        dishes=(MealPlanDish(name=dish, category="主菜", main_ingredient="牛肉"),),
        source="single",
    )


def test_monday_of_は週の月曜を返す() -> None:
    # 2026-05-25 は月曜
    assert monday_of(date(2026, 5, 25)) == date(2026, 5, 25)
    # 2026-05-27 (水) も同じ月曜に丸まる
    assert monday_of(date(2026, 5, 27)) == date(2026, 5, 25)
    # 2026-05-31 (日)
    assert monday_of(date(2026, 5, 31)) == date(2026, 5, 25)


def test_week_range_は月曜と日曜を返す() -> None:
    start, end = week_range(date(2026, 5, 27))
    assert start == date(2026, 5, 25)
    assert end == date(2026, 5, 31)


def test_today_in_jst_はJST基準() -> None:
    # UTC 2026-05-25 15:00 は JST 2026-05-26 00:00
    moment = datetime(2026, 5, 25, 15, 0, tzinfo=UTC)
    assert today_in_jst(moment) == date(2026, 5, 26)


def test_build_week_view_は7日分のセル生成() -> None:
    plans = [_plan(date(2026, 5, 25)), _plan(date(2026, 5, 27), status="cooked", dish="親子丼")]
    wv = build_week_view(plans, week_start=date(2026, 5, 25), today=date(2026, 5, 27))
    assert wv.week_start == date(2026, 5, 25)
    assert wv.week_end == date(2026, 5, 31)
    assert len(wv.days) == 7
    assert wv.days[0].plan_date == date(2026, 5, 25)
    assert wv.days[0].weekday_label == "月"
    assert wv.days[0].status == "confirmed"
    # 今日
    assert wv.days[2].is_today is True
    assert wv.days[2].status == "cooked"
    assert wv.days[2].primary_dish_name == "親子丼"
    # 未来日は空
    assert wv.days[6].status == "empty"
    assert wv.days[6].primary_dish_name is None


def test_build_week_view_は前後の週ナビ計算() -> None:
    wv = build_week_view([], week_start=date(2026, 5, 25), today=date(2026, 5, 25))
    assert wv.prev_week_start == date(2026, 5, 18)
    assert wv.next_week_start == date(2026, 6, 1)


def test_label_同月内() -> None:
    wv = build_week_view([], week_start=date(2026, 5, 25), today=date(2026, 5, 25))
    assert wv.label == "2026/05/25 - 05/31"


def test_build_month_view_は6週固定() -> None:
    plans = [_plan(date(2026, 5, 10), status="cooked")]
    mv = build_month_view(plans, year=2026, month=5, today=date(2026, 5, 10))
    assert mv.year == 2026
    assert mv.month == 5
    assert len(mv.weeks) == 6
    assert all(len(w) == 7 for w in mv.weeks)
    # 5/10 セルを探す
    all_cells = [c for w in mv.weeks for c in w]
    cell_510 = next(c for c in all_cells if c.plan_date == date(2026, 5, 10))
    assert cell_510.in_current_month is True
    assert cell_510.status == "cooked"
    # 前月末・翌月初は in_current_month=False になる
    other_month = [c for c in all_cells if not c.in_current_month]
    assert len(other_month) > 0


def test_month_range_dates_は6週分() -> None:
    start, end = month_range_dates(2026, 5)
    assert start == monday_of(date(2026, 5, 1))
    # 6 週 = 42 日
    assert (end - start).days == 41


def test_jst定数は_utc_offset_9() -> None:
    assert JST.utcoffset(None).total_seconds() == 9 * 3600  # type: ignore[union-attr]

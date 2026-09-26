"""ダッシュボード・カレンダー画面に渡す表示用データの整形サービス.

meal_plans の生データを「日のセル」「週」「月」という UI 都合の単位に組み替える.
週は月曜始まり (ISO 週) で固定. すべて Asia/Tokyo (JST) で日付を扱う.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from recipe_system.domain.meal_plan import MealPlan, MealPlanStatus

JST = timezone(timedelta(hours=9), name="JST")

_WEEKDAY_JP = ("月", "火", "水", "木", "金", "土", "日")


def today_in_jst(now: datetime | None = None) -> date:
    """JST 基準の今日の日付."""
    moment = now or datetime.now(tz=JST)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=JST)
    return moment.astimezone(JST).date()


def monday_of(target: date) -> date:
    """target を含む週の月曜日 (ISO 週開始日)."""
    return target - timedelta(days=target.weekday())


def week_range(start: date) -> tuple[date, date]:
    """週開始日 (月曜) と週末日 (日曜) を返す."""
    monday = monday_of(start)
    sunday = monday + timedelta(days=6)
    return monday, sunday


@dataclass(frozen=True)
class DayCell:
    """1 日分の表示セル."""

    plan_date: date
    weekday_label: str
    is_today: bool
    is_past: bool
    in_current_month: bool
    meal_plan: MealPlan | None

    @property
    def status(self) -> MealPlanStatus:
        return self.meal_plan.status if self.meal_plan else "empty"

    @property
    def primary_dish_name(self) -> str | None:
        if self.meal_plan and self.meal_plan.dishes:
            return self.meal_plan.dishes[0].name
        return None

    @property
    def iso(self) -> str:
        return self.plan_date.isoformat()


@dataclass(frozen=True)
class WeekView:
    """7 日分のセルを保持する週ビュー."""

    week_start: date
    week_end: date
    days: tuple[DayCell, ...]

    @property
    def prev_week_start(self) -> date:
        return self.week_start - timedelta(days=7)

    @property
    def next_week_start(self) -> date:
        return self.week_start + timedelta(days=7)

    @property
    def label(self) -> str:
        """ヘッダ表示用の期間ラベル. 例: 2026/05/25 - 05/31."""
        if self.week_start.year == self.week_end.year:
            return (
                f"{self.week_start.year}/{self.week_start.month:02d}/{self.week_start.day:02d}"
                f" - {self.week_end.month:02d}/{self.week_end.day:02d}"
            )
        return f"{self.week_start.isoformat()} - {self.week_end.isoformat()}"


@dataclass(frozen=True)
class MonthView:
    """月ビュー. 週 (月曜開始) のリストで保持する.

    前月末・翌月初の日も含めて 6 週 (42 セル) を埋める Google カレンダー形式.
    """

    year: int
    month: int
    weeks: tuple[tuple[DayCell, ...], ...]

    @property
    def label(self) -> str:
        return f"{self.year}年{self.month}月"


def build_week_view(
    plans: Iterable[MealPlan],
    *,
    week_start: date,
    today: date,
) -> WeekView:
    """meal_plans 列から WeekView を組み立てる.

    plans は week_start 含む 7 日分を想定するが, 範囲外の plan は無視する.
    week_start は月曜であることを呼び出し側で保証する.
    """
    monday = monday_of(week_start)
    sunday = monday + timedelta(days=6)
    plans_by_date = {plan.plan_date: plan for plan in plans}
    cells: list[DayCell] = []
    for offset in range(7):
        d = monday + timedelta(days=offset)
        cells.append(
            DayCell(
                plan_date=d,
                weekday_label=_WEEKDAY_JP[d.weekday()],
                is_today=(d == today),
                is_past=(d < today),
                in_current_month=True,
                meal_plan=plans_by_date.get(d),
            )
        )
    return WeekView(week_start=monday, week_end=sunday, days=tuple(cells))


def build_month_view(
    plans: Iterable[MealPlan],
    *,
    year: int,
    month: int,
    today: date,
) -> MonthView:
    """指定年月の月カレンダーを 6 週固定 (42 セル) で生成する."""
    first_day = date(year, month, 1)
    grid_start = monday_of(first_day)
    plans_by_date = {plan.plan_date: plan for plan in plans}
    weeks: list[tuple[DayCell, ...]] = []
    for w in range(6):
        row: list[DayCell] = []
        for offset in range(7):
            d = grid_start + timedelta(days=w * 7 + offset)
            row.append(
                DayCell(
                    plan_date=d,
                    weekday_label=_WEEKDAY_JP[d.weekday()],
                    is_today=(d == today),
                    is_past=(d < today),
                    in_current_month=(d.month == month and d.year == year),
                    meal_plan=plans_by_date.get(d),
                )
            )
        weeks.append(tuple(row))
    return MonthView(year=year, month=month, weeks=tuple(weeks))


def month_range_dates(year: int, month: int) -> tuple[date, date]:
    """指定月の表示グリッド全体 (前月末〜翌月初を含む 6 週分) の開始・終了日."""
    first_day = date(year, month, 1)
    grid_start = monday_of(first_day)
    grid_end = grid_start + timedelta(days=6 * 7 - 1)
    return grid_start, grid_end

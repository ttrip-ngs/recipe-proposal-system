"""既存 history と proposals.accepted=True を meal_plans に backfill するマイグレーション.

実行例:
    uv run python scripts/migrate_history_to_meal_plans.py --emulator
    uv run python scripts/migrate_history_to_meal_plans.py --project recipe-system-prod --apply

デフォルトは dry-run. --apply を付けた時のみ Firestore に書き込む.

データソースの優先順位:
  1. proposals コレクション (accepted=True) のうち requested_at が日付確定しているもの
     - dishes 等が完全なので status=cooked + dishes + proposal_id を埋められる
  2. history コレクション (cooked_at, recipe_name) のもの
     - dishes は recipe_name のみ復元できる (category 等は不明)
     - すでに proposals 由来で同じ日付が埋まっていたらスキップ

タイムゾーンは Asia/Tokyo (JST) で日付確定する.
本番運用は履歴を多数持つ可能性があるので一度だけ実行することを想定.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from google.cloud import firestore  # noqa: E402

from recipe_system.domain import MealPlan, MealPlanDish  # noqa: E402
from recipe_system.repository.meal_plan_repository import (  # noqa: E402
    get_meal_plan,
    upsert_meal_plan,
)

JST = timezone(timedelta(hours=9), name="JST")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="history -> meal_plans backfill")
    parser.add_argument("--emulator", action="store_true", help="Firestore エミュレータに接続")
    parser.add_argument("--project", default=None, help="GCP プロジェクト ID")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="付けると実書き込み. 省略時は dry-run.",
    )
    parser.add_argument(
        "--family-id",
        default=None,
        help="特定の family_id のみ対象にする (省略時は全家族)",
    )
    return parser.parse_args()


def _ensure_emulator() -> None:
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        return
    os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"


def _to_jst_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(JST).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(JST).date()
        except ValueError:
            return None
    return None


def _dish_from_proposal_record(record: dict[str, Any]) -> MealPlanDish:
    return MealPlanDish(
        name=record.get("name", ""),
        category=record.get("category", "主菜"),
        main_ingredient=record.get("main_ingredient", ""),
        reason=record.get("reason"),
        ingredients=tuple(record.get("ingredients", [])),
    )


def _migrate_proposals(
    client: firestore.Client,
    *,
    family_filter: str | None,
    apply: bool,
) -> dict[tuple[str, date], MealPlan]:
    """accepted=True の proposals を MealPlan に変換して返す.

    apply=True の場合はそのまま upsert する.
    返り値は (family_id, plan_date) -> MealPlan のマップで, 次段の history マージで衝突回避に使う.
    """
    query = client.collection("proposals").where("accepted", "==", True)
    by_key: dict[tuple[str, date], MealPlan] = {}
    count = 0
    for doc in query.stream():
        data = doc.to_dict() or {}
        family_id = data.get("family_id")
        if not family_id:
            continue
        if family_filter and family_id != family_filter:
            continue
        plan_date = _to_jst_date(data.get("requested_at"))
        if plan_date is None:
            continue
        dishes_raw = data.get("dishes") or []
        dishes = tuple(_dish_from_proposal_record(d) for d in dishes_raw)
        plan = MealPlan(
            family_id=family_id,
            plan_date=plan_date,
            status="cooked",
            proposal_id=doc.id,
            dishes=dishes,
            source="single",
        )
        by_key[(family_id, plan_date)] = plan
        if apply:
            upsert_meal_plan(client, plan)
        count += 1
        print(f"  proposals -> {family_id}/{plan_date} ({len(dishes)} dishes) doc={doc.id}")
    print(f"[proposals] migrated={count}")
    return by_key


def _migrate_history(
    client: firestore.Client,
    *,
    family_filter: str | None,
    apply: bool,
    already: dict[tuple[str, date], MealPlan],
) -> int:
    """history を MealPlan(status=cooked) に変換.

    proposals 由来で既に同じ日付がある場合はスキップ (情報量が多い方を優先).
    Firestore に同一 doc が存在する場合も上書きしない (idempotent 性確保).
    """
    query = client.collection("history")
    if family_filter:
        query = query.where("family_id", "==", family_filter)
    count = 0
    for doc in query.stream():
        data = doc.to_dict() or {}
        family_id = data.get("family_id")
        if not family_id:
            continue
        plan_date = _to_jst_date(data.get("cooked_at"))
        if plan_date is None:
            continue
        if (family_id, plan_date) in already:
            continue
        existing = get_meal_plan(client, family_id, plan_date)
        if existing is not None and existing.status == "cooked":
            continue
        recipe_name = data.get("recipe_name") or data.get("recipe_id") or "(不明)"
        plan = MealPlan(
            family_id=family_id,
            plan_date=plan_date,
            status="cooked",
            dishes=(
                MealPlanDish(
                    name=recipe_name,
                    category="主菜",
                    main_ingredient="",
                    reason=data.get("notes"),
                ),
            ),
            source="manual",
            notes=data.get("notes"),
        )
        if apply:
            upsert_meal_plan(client, plan)
        count += 1
        print(f"  history -> {family_id}/{plan_date} name={recipe_name}")
    print(f"[history] migrated={count}")
    return count


def main() -> int:
    args = _parse_args()
    if args.emulator:
        _ensure_emulator()
        print(f"[mode] emulator (host={os.environ['FIRESTORE_EMULATOR_HOST']})")
    else:
        print(f"[mode] project={args.project or '(ADC default)'}")
    if not args.apply:
        print("[dry-run] --apply を付けると実書き込み")

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT") or "recipe-system-dev"
    client = firestore.Client(project=project)

    already = _migrate_proposals(
        client,
        family_filter=args.family_id,
        apply=args.apply,
    )
    _migrate_history(
        client,
        family_filter=args.family_id,
        apply=args.apply,
        already=already,
    )
    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

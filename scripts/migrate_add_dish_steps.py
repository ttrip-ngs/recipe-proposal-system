"""料理 (dishes) に作り方 (steps) フィールドを追加するマイグレーション (2026-09).

対象:
  - meal_plans/{id}.dishes[].steps
  - proposals/{id}.dishes[].steps          (単日提案)
  - proposals/{id}.days[].dishes[].steps   (週間提案)

steps を持たない料理に空配列を入れる. 読み出し側 (MealPlanDish.steps) は既定値 ()
で旧ドキュメントも読めるため必須ではないが、コレクション内の形を揃えて
Firestore コンソール・エクスポートで欠損と区別できるようにする. 既に steps を
持つ料理は変更しない (冪等).

実行例:
    uv run python scripts/migrate_add_dish_steps.py --emulator
    uv run python scripts/migrate_add_dish_steps.py --emulator --apply

本番への適用手順:
  1. `gcloud auth application-default login` で本番プロジェクトの権限を持つアカウントに切替
  2. dry-run で件数を確認する:
       uv run python scripts/migrate_add_dish_steps.py --project <本番プロジェクト ID>
  3. 手順導入後のアプリをデプロイしてから --apply を付けて実行する
     (旧アプリは steps を知らないが、未知フィールドは無視されるため順序が逆でも壊れない)
  4. 再度 dry-run して対象 0 件になったことを確認する

デフォルトは dry-run. --apply を付けた時のみ Firestore に書き込む.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from google.cloud import firestore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="dishes[].steps backfill")
    parser.add_argument("--emulator", action="store_true", help="Firestore エミュレータに接続")
    parser.add_argument("--project", default=None, help="GCP プロジェクト ID")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="付けると実書き込み. 省略時は dry-run.",
    )
    return parser.parse_args()


def _fill_steps(dishes: list[dict[str, Any]]) -> bool:
    """steps の無い料理に空配列を入れる. 1 件でも変更したら True."""
    changed = False
    for dish in dishes:
        if "steps" not in dish:
            dish["steps"] = []
            changed = True
    return changed


def _migrate_meal_plans(client: firestore.Client, *, apply: bool) -> int:
    count = 0
    for doc in client.collection("meal_plans").stream():
        data = doc.to_dict() or {}
        dishes = data.get("dishes", [])
        if _fill_steps(dishes):
            count += 1
            if apply:
                doc.reference.update({"dishes": dishes})
    return count


def _migrate_proposals(client: firestore.Client, *, apply: bool) -> int:
    count = 0
    for doc in client.collection("proposals").stream():
        data = doc.to_dict() or {}
        update: dict[str, Any] = {}
        dishes = data.get("dishes", [])
        if _fill_steps(dishes):
            update["dishes"] = dishes
        days = data.get("days", [])
        # any() は短絡評価で残りの日を埋めないため、全日を先に処理してから判定する.
        if any([_fill_steps(day.get("dishes", [])) for day in days]):
            update["days"] = days
        if update:
            count += 1
            if apply:
                doc.reference.update(update)
    return count


def main() -> int:
    args = _parse_args()
    if args.emulator:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
        print(f"[mode] emulator (host={os.environ['FIRESTORE_EMULATOR_HOST']})")
    else:
        print(f"[mode] project={args.project or '(ADC default)'}")
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT") or "recipe-system-dev"
    client = firestore.Client(project=project)

    label = "updated" if args.apply else "would update (dry-run)"
    print(f"[meal_plans] {label}: {_migrate_meal_plans(client, apply=args.apply)}")
    print(f"[proposals] {label}: {_migrate_proposals(client, apply=args.apply)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

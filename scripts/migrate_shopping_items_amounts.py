"""shopping_lists.items[] を旧形式 (total_quantity / unit) から新形式 (amounts / pantry) へ変換する.

実行例:
    uv run python scripts/migrate_shopping_items_amounts.py --emulator
    uv run python scripts/migrate_shopping_items_amounts.py --project recipe-system-prod --apply

デフォルトは dry-run. --apply を付けた時のみ Firestore に書き込む.

変換内容 (item 単位):
    total_quantity / unit  ->  amounts = [{quantity, unit}] (total_quantity が null なら [])
    pantry が無ければ false を付与
変換は形式のみ. 重複行の統合や水・常備品の振り分けは行わないので, 必要なら画面の
「集約を再計算」で作り直す (手動追加・チェック状態は引き継がれる).

本番適用手順:
    1. 新コードをデプロイする (旧形式も読めるため, デプロイと migration の順序は問わない)
    2. dry-run で対象件数を確認する
         uv run python scripts/migrate_shopping_items_amounts.py --project <本番ID>
    3. --apply を付けて実行する. 変換済みの item はスキップするので再実行しても安全
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from google.cloud import firestore

COLLECTION = "shopping_lists"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="shopping_lists items -> amounts 形式")
    parser.add_argument("--emulator", action="store_true", help="Firestore エミュレータに接続")
    parser.add_argument("--project", default=None, help="GCP プロジェクト ID")
    parser.add_argument(
        "--apply", action="store_true", help="付けると実書き込み. 省略時は dry-run."
    )
    return parser.parse_args()


def convert_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """旧形式の item を新形式に変換する. 変換不要なら None."""
    if "amounts" in item and "pantry" in item:
        return None
    new = {k: v for k, v in item.items() if k not in ("total_quantity", "unit")}
    if "amounts" not in item:
        qty = item.get("total_quantity")
        new["amounts"] = [] if qty is None else [{"quantity": qty, "unit": item.get("unit")}]
    new.setdefault("pantry", False)
    return new


def main() -> None:
    args = _parse_args()
    if args.emulator:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
        print(f"[mode] emulator (host={os.environ['FIRESTORE_EMULATOR_HOST']})")
    else:
        print(f"[mode] project={args.project or '(ADC default)'}")
    print(f"[mode] {'APPLY' if args.apply else 'dry-run'}")

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT") or "recipe-system-dev"
    client = firestore.Client(project=project)

    docs_changed = 0
    for doc in client.collection(COLLECTION).stream():
        items = (doc.to_dict() or {}).get("items", [])
        converted = [convert_item(it) for it in items]
        changed = sum(1 for c in converted if c is not None)
        if changed == 0:
            continue
        new_items = [c if c is not None else it for c, it in zip(converted, items, strict=True)]
        print(f"  {doc.id}: {changed}/{len(items)} items")
        if args.apply:
            doc.reference.update({"items": new_items})
        docs_changed += 1
    print(f"[shopping_lists] docs_to_update={docs_changed}")


if __name__ == "__main__":
    main()

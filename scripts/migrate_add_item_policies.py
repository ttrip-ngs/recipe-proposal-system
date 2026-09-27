"""家族メンバーに item_policies (通常は除去不要な食品の可/不可) を追加する (2026-09).

対象:
  - families/{id}/members/{id}.item_policies

item_policies を持たないメンバーに空の map を入れ, 可/不可が未選択の食品を一覧表示する.
未選択はガードレールで除去扱い (ADR 0007) のため, 空の map を入れても判定は変わらない.
未選択を明示的な block で埋めることはしない (画面で「選んだ除去」と「未選択」を区別できなく
なるため). 既に item_policies を持つメンバーは変更しない (冪等).

実行例:
    uv run python scripts/migrate_add_item_policies.py --emulator
    uv run python scripts/migrate_add_item_policies.py --emulator --apply

本番への適用手順:
  1. `gcloud auth application-default login` で本番プロジェクトの権限を持つアカウントに切替
  2. dry-run で対象メンバーと未選択の食品を確認する:
       uv run python scripts/migrate_add_item_policies.py --project <本番プロジェクト ID>
  3. 本機能を含むアプリをデプロイしてから --apply を付けて実行する
     (旧アプリは item_policies を知らないが, 未知フィールドは無視されるため順序が逆でも壊れない)
  4. デプロイ直後から, 未選択の食品 (小麦アレルギーの醤油・味噌など) は除去扱いになり, それらを
     使う料理が提案されなくなる. 小麦の「酢」は部分一致で検査されるため, 米酢・黒酢・ポン酢
     なども止まる. 手順 2 の一覧をもとに, 家族設定画面ですぐに可/不可を選ぶ
  5. 「旧データ」と表示されたメンバーは, allergens に除去不要候補そのもの (味噌など) を持つ.
     判定では引き続き除去されるが, 編集画面にそのチェックボックスは無く, 保存すると消える.
     例えば味噌なら, 大豆アレルギーとして選び直し, 味噌を「除去する」にする
  6. 再度 dry-run して, 未選択が残っていないことを確認する

デフォルトは dry-run. --apply を付けた時のみ Firestore に書き込む.
メンバー名はセンシティブデータのため表示せず, member_id のみ出す.
"""

from __future__ import annotations

import argparse
import os
import sys

from google.cloud import firestore

from recipe_system.guardrails.dictionary_loader import default_dictionary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="members[].item_policies backfill")
    parser.add_argument("--emulator", action="store_true", help="Firestore エミュレータに接続")
    parser.add_argument("--project", default=None, help="GCP プロジェクト ID")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="付けると実書き込み. 省略時は dry-run.",
    )
    return parser.parse_args()


def _undecided(allergens: list[str], policies: dict[str, dict[str, str]]) -> list[str]:
    dic = default_dictionary()
    return [
        f"{a}の{item}"
        for a in sorted(allergens)
        for item in sorted(dic.tolerable_items(a))
        if item not in policies.get(a, {})
    ]


def _tolerated_items() -> set[str]:
    return set().union(*default_dictionary().allergen_group_tolerated.values())


def _migrate_members(client: firestore.Client, *, apply: bool) -> tuple[int, int]:
    """(item_policies を追加したメンバー数, 未選択が残るメンバー数)."""
    added = undecided_members = 0
    for family in client.collection("families").stream():
        for member in family.reference.collection("members").stream():
            data = member.to_dict() or {}
            if data.get("item_policies") is None:  # 欠損または null
                added += 1
                if apply:
                    member.reference.update({"item_policies": {}})
            allergens = data.get("allergens") or []
            undecided = _undecided(allergens, data.get("item_policies") or {})
            legacy = sorted(set(allergens) & _tolerated_items())
            if legacy:
                print(f"  {family.id}/{member.id}: 旧データ allergens に {', '.join(legacy)}")
            if undecided:
                undecided_members += 1
                print(f"  {family.id}/{member.id}: 未選択 {', '.join(undecided)}")
    return added, undecided_members


def main() -> int:
    args = _parse_args()
    if args.emulator:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
        print(f"[mode] emulator (host={os.environ['FIRESTORE_EMULATOR_HOST']})")
    else:
        print(f"[mode] project={args.project or '(ADC default)'}")
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT") or "recipe-system-dev"
    client = firestore.Client(project=project)

    added, undecided = _migrate_members(client, apply=args.apply)
    label = "updated" if args.apply else "would update (dry-run)"
    print(f"[members] {label}: {added}")
    print(f"[members] 可/不可が未選択のメンバー: {undecided} (家族設定画面で選ぶこと)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

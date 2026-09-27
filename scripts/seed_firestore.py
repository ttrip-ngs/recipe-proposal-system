"""Firestore へ初期データを投入するスクリプト.

使い方:
    uv run python scripts/seed_firestore.py --emulator
    uv run python scripts/seed_firestore.py --project recipe-system-dev

--emulator を付けるとローカル Firestore エミュレータに書き込む.
本番家族データは --family data/seeds/family.production.yaml で明示的に指定する
(デフォルトは family.example.yaml).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from google.cloud import firestore  # noqa: E402

DEFAULT_SEEDS_DIR = REPO_ROOT / "data" / "seeds"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Firestore 初期データ投入")
    parser.add_argument("--emulator", action="store_true", help="Firestore エミュレータに書き込む")
    parser.add_argument("--project", default=None, help="GCP プロジェクト ID")
    parser.add_argument(
        "--family",
        type=Path,
        default=DEFAULT_SEEDS_DIR / "family.example.yaml",
        help="家族プロファイル YAML",
    )
    parser.add_argument(
        "--recipes",
        type=Path,
        default=DEFAULT_SEEDS_DIR / "recipes.example.yaml",
        help="レシピ YAML",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=DEFAULT_SEEDS_DIR / "history.example.yaml",
        help="履歴 YAML",
    )
    return parser.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_emulator() -> None:
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        return
    os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"


def _seed_family(client: firestore.Client, data: dict[str, Any]) -> None:
    family_id = data["family_id"]
    now = datetime.now(UTC)
    family_doc = {
        "name": data["name"],
        "timezone": data.get("timezone", "Asia/Tokyo"),
        "allowed_emails": data.get("allowed_emails", []),
        "created_at": now,
        "updated_at": now,
    }
    client.collection("families").document(family_id).set(family_doc, merge=True)

    for member in data.get("members", []):
        member_doc = {
            "name": member["name"],
            "role": member.get("role"),
            "allergens": member.get("allergens", []),
            "item_policies": member.get("item_policies", {}),
            "dislikes": member.get("dislikes", []),
            "likes": member.get("likes", []),
            "notes": member.get("notes"),
            "reviewed_at": member["reviewed_at"],
        }
        (
            client.collection("families")
            .document(family_id)
            .collection("members")
            .document(member["member_id"])
            .set(member_doc, merge=True)
        )
    print(f"[family] {family_id} members={len(data.get('members', []))}")


def _seed_recipes(client: firestore.Client, data: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    count = 0
    for recipe in data.get("recipes", []):
        doc = {
            "name": recipe["name"],
            "category": recipe["category"],
            "main_ingredient": recipe["main_ingredient"],
            "ingredients": recipe.get("ingredients", []),
            "steps": recipe.get("steps", []),
            "tags": recipe.get("tags", []),
            "servings": recipe.get("servings", 4),
            "source": recipe.get("source"),
            "created_at": now,
        }
        client.collection("recipes").add(doc)
        count += 1
    print(f"[recipes] inserted={count}")


def _seed_history(client: firestore.Client, data: dict[str, Any]) -> None:
    count = 0
    for entry in data.get("history", []):
        doc = {
            "family_id": entry["family_id"],
            "recipe_name": entry["recipe_name"],
            "cooked_at": entry["cooked_at"],
            "rating": entry.get("rating", 0),
            "notes": entry.get("notes"),
        }
        client.collection("history").add(doc)
        count += 1
    print(f"[history] inserted={count}")


def _seed_llm_budget(client: firestore.Client) -> None:
    """LLM 月次予算ドキュメントの初期値.

    既存ドキュメントを保護するため ``merge=True`` で書く. 運用後は Firestore
    コンソール直接編集で運用変更する想定 (`config/llm_budget.monthly_jpy_limit`).
    """
    client.collection("config").document("llm_budget").set(
        {
            "monthly_jpy_limit": 3000.0,
            "warn_threshold_pct": 80,
            "updated_at": datetime.now(UTC),
        },
        merge=True,
    )
    print("[config/llm_budget] seeded (merge)")


def main() -> int:
    args = _parse_args()
    if args.emulator:
        _ensure_emulator()
        print(f"[mode] emulator (host={os.environ['FIRESTORE_EMULATOR_HOST']})")
    else:
        print(f"[mode] project={args.project or '(ADC default)'}")

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT") or "recipe-system-dev"
    client = firestore.Client(project=project)

    _seed_family(client, _load_yaml(args.family))
    _seed_recipes(client, _load_yaml(args.recipes))
    _seed_history(client, _load_yaml(args.history))
    _seed_llm_budget(client)
    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""meal_plans コレクションのアクセス層.

ドキュメント ID は {family_id}_{YYYY-MM-DD} で固定. これにより
家族 x 日付 x 食事枠 (dinner 固定) のユニーク制約を Firestore 側で担保する.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from recipe_system.domain.meal_plan import (
    MealPlan,
    MealPlanDish,
    MealPlanSource,
    MealPlanStatus,
    make_plan_id,
)
from recipe_system.repository.converters import to_date, to_datetime_or_none

COLLECTION = "meal_plans"


def get_meal_plan(
    client: firestore.Client,
    family_id: str,
    plan_date: date,
) -> MealPlan | None:
    """指定日の予定を取得. 存在しなければ None."""
    doc = client.collection(COLLECTION).document(make_plan_id(family_id, plan_date)).get()
    if not doc.exists:
        return None
    return _to_model(doc.to_dict() or {})


def list_meal_plans_in_range(
    client: firestore.Client,
    family_id: str,
    *,
    start_date: date,
    end_date: date,
) -> tuple[MealPlan, ...]:
    """[start_date, end_date] (両端含む) の予定を日付昇順で返す."""
    query = (
        client.collection(COLLECTION)
        .where(filter=FieldFilter("family_id", "==", family_id))
        .where(filter=FieldFilter("plan_date", ">=", start_date.isoformat()))
        .where(filter=FieldFilter("plan_date", "<=", end_date.isoformat()))
        .order_by("plan_date", direction=firestore.Query.ASCENDING)
    )
    results: list[MealPlan] = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        results.append(_to_model(data))
    return tuple(results)


def upsert_meal_plan(client: firestore.Client, meal_plan: MealPlan) -> str:
    """予定を新規作成または上書き. ドキュメント ID を返す."""
    now = datetime.now(UTC)
    doc_id = meal_plan.plan_id
    ref = client.collection(COLLECTION).document(doc_id)
    existing = ref.get()
    payload = _to_record(meal_plan)
    if existing.exists:
        existing_data = existing.to_dict() or {}
        payload["created_at"] = existing_data.get("created_at") or now
    else:
        payload["created_at"] = meal_plan.created_at or now
    payload["updated_at"] = now
    ref.set(payload)
    return doc_id


def update_status(
    client: firestore.Client,
    family_id: str,
    plan_date: date,
    *,
    status: MealPlanStatus,
    proposal_id: str | None = None,
    dishes: tuple[MealPlanDish, ...] | None = None,
    source: MealPlanSource | None = None,
    notes: str | None = None,
) -> None:
    """部分更新. 存在しないドキュメントには適用せず作成する.

    proposal_id / dishes / source / notes は None ならフィールドを変更しない.
    """
    doc_id = make_plan_id(family_id, plan_date)
    ref = client.collection(COLLECTION).document(doc_id)
    existing = ref.get()
    now = datetime.now(UTC)
    if not existing.exists:
        plan = MealPlan(
            family_id=family_id,
            plan_date=plan_date,
            status=status,
            proposal_id=proposal_id,
            dishes=dishes or (),
            source=source or "manual",
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        ref.set(_to_record(plan) | {"created_at": now, "updated_at": now})
        return

    patch: dict[str, Any] = {"status": status, "updated_at": now}
    if proposal_id is not None:
        patch["proposal_id"] = proposal_id
    if dishes is not None:
        patch["dishes"] = [_dish_to_record(d) for d in dishes]
    if source is not None:
        patch["source"] = source
    if notes is not None:
        patch["notes"] = notes
    ref.update(patch)


def delete_meal_plan(client: firestore.Client, family_id: str, plan_date: date) -> None:
    client.collection(COLLECTION).document(make_plan_id(family_id, plan_date)).delete()


def _to_model(data: dict[str, Any]) -> MealPlan:
    plan_date = to_date(data.get("plan_date"))
    dishes = tuple(MealPlanDish(**d) for d in data.get("dishes", []))
    return MealPlan(
        family_id=data["family_id"],
        plan_date=plan_date,
        slot=data.get("slot", "dinner"),
        status=data.get("status", "empty"),
        proposal_id=data.get("proposal_id"),
        dishes=dishes,
        source=data.get("source", "manual"),
        notes=data.get("notes"),
        created_at=to_datetime_or_none(data.get("created_at")),
        updated_at=to_datetime_or_none(data.get("updated_at")),
    )


def _to_record(meal_plan: MealPlan) -> dict[str, Any]:
    return {
        "family_id": meal_plan.family_id,
        "plan_date": meal_plan.plan_date.isoformat(),
        "slot": meal_plan.slot,
        "status": meal_plan.status,
        "proposal_id": meal_plan.proposal_id,
        "dishes": [_dish_to_record(d) for d in meal_plan.dishes],
        "source": meal_plan.source,
        "notes": meal_plan.notes,
    }


def _dish_to_record(dish: MealPlanDish) -> dict[str, Any]:
    return {
        "name": dish.name,
        "category": dish.category,
        "main_ingredient": dish.main_ingredient,
        "reason": dish.reason,
        "ingredients": list(dish.ingredients),
        "steps": list(dish.steps),
    }

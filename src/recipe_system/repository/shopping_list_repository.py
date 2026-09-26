"""shopping_lists コレクションのアクセス層."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from google.cloud import firestore

from recipe_system.domain.shopping_list import (
    ShoppingAmount,
    ShoppingItem,
    ShoppingList,
    make_shopping_list_id,
)
from recipe_system.repository.converters import to_date, to_datetime_or_none

COLLECTION = "shopping_lists"


def get_shopping_list(
    client: firestore.Client,
    family_id: str,
    week_start: date,
) -> ShoppingList | None:
    doc = client.collection(COLLECTION).document(make_shopping_list_id(family_id, week_start)).get()
    if not doc.exists:
        return None
    return _to_model(doc.to_dict() or {})


def upsert_shopping_list(client: firestore.Client, shopping_list: ShoppingList) -> str:
    now = datetime.now(UTC)
    doc_id = shopping_list.list_id
    ref = client.collection(COLLECTION).document(doc_id)
    existing = ref.get()
    payload = _to_record(shopping_list)
    if existing.exists:
        existing_data = existing.to_dict() or {}
        payload["generated_at"] = existing_data.get("generated_at") or now
    else:
        payload["generated_at"] = shopping_list.generated_at or now
    payload["updated_at"] = now
    ref.set(payload)
    return doc_id


def toggle_item(
    client: firestore.Client,
    family_id: str,
    week_start: date,
    item_id: str,
) -> ShoppingList | None:
    """指定 item の checked を反転."""
    existing = get_shopping_list(client, family_id, week_start)
    if existing is None:
        return None
    new_items = tuple(
        it.model_copy(update={"checked": not it.checked}) if it.item_id == item_id else it
        for it in existing.items
    )
    updated = existing.model_copy(update={"items": new_items})
    upsert_shopping_list(client, updated)
    return updated


def add_item(
    client: firestore.Client,
    family_id: str,
    week_start: date,
    item: ShoppingItem,
) -> ShoppingList:
    """手動アイテムを追加 (manual=True を強制)."""
    existing = get_shopping_list(client, family_id, week_start)
    item = item.model_copy(update={"manual": True})
    if existing is None:
        from datetime import timedelta

        existing = ShoppingList(
            family_id=family_id,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            items=(item,),
        )
    else:
        existing = existing.model_copy(update={"items": (*existing.items, item)})
    upsert_shopping_list(client, existing)
    return existing


def remove_item(
    client: firestore.Client,
    family_id: str,
    week_start: date,
    item_id: str,
) -> ShoppingList | None:
    existing = get_shopping_list(client, family_id, week_start)
    if existing is None:
        return None
    new_items = tuple(it for it in existing.items if it.item_id != item_id)
    updated = existing.model_copy(update={"items": new_items})
    upsert_shopping_list(client, updated)
    return updated


def _to_model(data: dict[str, Any]) -> ShoppingList:
    items_raw = data.get("items", [])
    items = tuple(
        ShoppingItem(
            item_id=i["item_id"],
            canonical=i["canonical"],
            raw_names=tuple(i.get("raw_names", [])),
            amounts=_amounts_from_record(i),
            pantry=bool(i.get("pantry", False)),
            checked=bool(i.get("checked", False)),
            manual=bool(i.get("manual", False)),
            note=i.get("note"),
        )
        for i in items_raw
    )
    return ShoppingList(
        family_id=data["family_id"],
        week_start=to_date(data["week_start"]),
        week_end=to_date(data["week_end"]),
        items=items,
        generated_at=to_datetime_or_none(data.get("generated_at")),
        updated_at=to_datetime_or_none(data.get("updated_at")),
    )


def _amounts_from_record(item: dict[str, Any]) -> tuple[ShoppingAmount, ...]:
    """amounts を読む. migration 前の旧形式 (total_quantity / unit) も読めるようにする."""
    if "amounts" in item:
        return tuple(ShoppingAmount(**a) for a in item["amounts"])
    if item.get("total_quantity") is None:
        return ()
    return (ShoppingAmount(quantity=item["total_quantity"], unit=item.get("unit")),)


def _to_record(shopping_list: ShoppingList) -> dict[str, Any]:
    return {
        "family_id": shopping_list.family_id,
        "week_start": shopping_list.week_start.isoformat(),
        "week_end": shopping_list.week_end.isoformat(),
        "iso_week": shopping_list.iso_week,
        "items": [
            {
                "item_id": it.item_id,
                "canonical": it.canonical,
                "raw_names": list(it.raw_names),
                "amounts": [a.model_dump() for a in it.amounts],
                "pantry": it.pantry,
                "checked": it.checked,
                "manual": it.manual,
                "note": it.note,
            }
            for it in shopping_list.items
        ],
    }

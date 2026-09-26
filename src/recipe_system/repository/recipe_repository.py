"""recipes コレクションのアクセス層."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore

from recipe_system.domain import Ingredient, Recipe
from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, default_dictionary


def list_recipes(
    client: firestore.Client,
    *,
    limit: int | None = None,
    dictionary: NormalizerDictionary | None = None,
) -> tuple[Recipe, ...]:
    dic = dictionary or default_dictionary()
    query: firestore.Query = client.collection("recipes")
    if limit is not None:
        query = query.limit(limit)
    return tuple(_to_recipe(doc.id, doc.to_dict() or {}, dic) for doc in query.stream())


def _to_recipe(recipe_id: str, data: dict[str, Any], dic: NormalizerDictionary) -> Recipe:
    ingredients = []
    for raw in data.get("ingredients", []):
        name = raw.get("name", "")
        canonical, tags = dic.normalize(name)
        ingredients.append(
            Ingredient(
                name=name,
                canonical=canonical,
                allergen_tags=tags,
                quantity=raw.get("quantity"),
                unit=raw.get("unit"),
            )
        )

    created_at = data.get("created_at")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    elif isinstance(created_at, datetime) and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    return Recipe(
        recipe_id=recipe_id,
        name=data["name"],
        category=data["category"],
        main_ingredient=data["main_ingredient"],
        ingredients=tuple(ingredients),
        steps=tuple(data.get("steps", [])),
        tags=frozenset(data.get("tags", [])),
        servings=int(data.get("servings", 4)),
        source=data.get("source"),
        created_at=created_at,
    )

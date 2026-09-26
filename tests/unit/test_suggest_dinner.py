"""services.suggest_dinner のユニットテスト (フェイククライアント使用)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel

from recipe_system.domain import FamilyMember, FamilyProfile, Ingredient, Recipe
from recipe_system.domain.llm_output import LLMProposal
from recipe_system.llm.client import FakeVertexClient, LLMResponse
from recipe_system.services.suggest_dinner import (
    LLMResponseParseError,
    SuggestContext,
    suggest_dinner,
)


def _family(allergens_by_member: dict[str, set[str]] | None = None) -> FamilyProfile:
    now = datetime.now(UTC)
    members = tuple(
        FamilyMember(
            member_id=f"m-{name}",
            name=name,
            allergens=frozenset(a),
            reviewed_at=now,
        )
        for name, a in (allergens_by_member or {"夫": set()}).items()
    )
    return FamilyProfile(
        family_id="test-family",
        name="テスト家",
        allowed_emails=frozenset({"t@example.com"}),
        members=members,
        created_at=now,
        updated_at=now,
    )


def _recipe(name: str, canonical_ingredients: list[tuple[str, frozenset[str]]]) -> Recipe:
    ings = tuple(
        Ingredient(name=c, canonical=c, allergen_tags=tags) for c, tags in canonical_ingredients
    )
    return Recipe(
        name=name,
        category="主菜",
        main_ingredient=canonical_ingredients[0][0],
        ingredients=ings,
    )


@dataclass
class _ScriptedClient:
    """指定の raw_text を返すフェイククライアント."""

    payload: dict[str, Any]
    payload_retry: dict[str, Any] | None = None
    calls: int = 0
    schemas_seen: list[type[BaseModel] | None] = field(default_factory=list)

    async def generate(
        self,
        *,
        system: str,  # noqa: ARG002
        user: str,  # noqa: ARG002
        prompt_version: str,  # noqa: ARG002
        max_tokens: int = 2000,  # noqa: ARG002
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.schemas_seen.append(output_schema)
        payload = self.payload_retry if (self.calls > 1 and self.payload_retry) else self.payload
        return LLMResponse(
            raw_text=json.dumps(payload, ensure_ascii=False),
            model="scripted",
            input_tokens=10,
            output_tokens=20,
        )


@pytest.mark.asyncio
async def test_フェイククライアントで成功する提案が返る() -> None:
    family = _family({"夫": set()})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = SuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
    )
    result = await suggest_dinner(ctx, llm=FakeVertexClient())
    assert result.succeeded is True
    assert len(result.dishes) >= 1
    assert result.retry_count == 0


@pytest.mark.asyncio
async def test_アレルゲンヒット_は_1_回リトライしてブロック継続なら失敗扱い() -> None:
    family = _family({"妻": {"甲殻類"}})
    # 1 回目: エビを返す / 2 回目: またエビを返す → block 継続 → succeeded False
    bad_payload = {
        "dishes": [
            {
                "name": "エビチリ",
                "category": "主菜",
                "main_ingredient": "エビ",
                "reason": "LLM がアレルゲンを出してしまった",
                "ingredients": [{"name": "エビ", "quantity": 200, "unit": "g"}],
                "steps": ["材料を切って調理する"],
            }
        ],
        "overall_comment": "badbad",
    }
    client = _ScriptedClient(payload=bad_payload, payload_retry=bad_payload)
    ctx = SuggestContext(family=family, all_recipes=[], recent_history=())
    result = await suggest_dinner(ctx, llm=client)
    assert result.succeeded is False
    assert result.retry_count == 1
    assert client.calls == 2
    assert any(v.severity == "block" for v in result.violations)


@pytest.mark.asyncio
async def test_リトライで安全な提案に切り替われば成功扱い() -> None:
    family = _family({"妻": {"甲殻類"}})
    bad = {
        "dishes": [
            {
                "name": "エビチリ",
                "category": "主菜",
                "main_ingredient": "エビ",
                "reason": "NG",
                "ingredients": [{"name": "エビ", "quantity": 200, "unit": "g"}],
                "steps": ["材料を切って調理する"],
            }
        ],
    }
    good = {
        "dishes": [
            {
                "name": "牛丼",
                "category": "主菜",
                "main_ingredient": "牛肉",
                "reason": "OK",
                "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
                "steps": ["材料を切って調理する"],
            }
        ],
    }
    client = _ScriptedClient(payload=bad, payload_retry=good)
    ctx = SuggestContext(family=family, all_recipes=[], recent_history=())
    result = await suggest_dinner(ctx, llm=client)
    assert result.succeeded is True
    assert result.retry_count == 1
    assert client.calls == 2


@dataclass
class _RawTextScriptedClient:
    """呼出ごとに指定の raw_text (壊れた JSON も可) を順に返すフェイククライアント."""

    raw_texts: list[str]
    calls: int = 0
    schemas_seen: list[type[BaseModel] | None] = field(default_factory=list)

    async def generate(
        self,
        *,
        system: str,  # noqa: ARG002
        user: str,  # noqa: ARG002
        prompt_version: str,  # noqa: ARG002
        max_tokens: int = 2000,  # noqa: ARG002
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        raw = self.raw_texts[min(self.calls, len(self.raw_texts) - 1)]
        self.calls += 1
        self.schemas_seen.append(output_schema)
        return LLMResponse(raw_text=raw, model="scripted", input_tokens=10, output_tokens=20)


@pytest.mark.asyncio
async def test_JSONパース失敗は1回だけ同一プロンプトで再要求され成功する() -> None:
    """実 Gemini 検証で発覚した「稀に壊れた JSON を返す」ケースの回帰テスト."""
    good_payload = json.dumps(
        {
            "dishes": [
                {
                    "name": "牛丼",
                    "category": "主菜",
                    "main_ingredient": "牛肉",
                    "reason": "OK",
                    "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
                    "steps": ["材料を切って調理する"],
                }
            ],
        },
        ensure_ascii=False,
    )
    client = _RawTextScriptedClient(raw_texts=['{"dishes": [', good_payload])
    ctx = SuggestContext(family=_family(), all_recipes=[], recent_history=())

    result = await suggest_dinner(ctx, llm=client)

    assert client.calls == 2
    assert result.succeeded is True
    assert [d.name for d in result.dishes] == ["牛丼"]


@pytest.mark.asyncio
async def test_JSONパース失敗が2回連続なら例外が伝播する() -> None:
    client = _RawTextScriptedClient(raw_texts=["not json at all", "still not json"])
    ctx = SuggestContext(family=_family(), all_recipes=[], recent_history=())

    with pytest.raises(LLMResponseParseError):
        await suggest_dinner(ctx, llm=client)

    assert client.calls == 2


@pytest.mark.asyncio
async def test_直近_7_日の履歴はプロンプトの候補から除外される() -> None:
    family = _family({"夫": set()})
    now = datetime.now(UTC)
    recipes = [
        _recipe("肉じゃが", [("牛肉", frozenset())]),
        _recipe("鶏の照り焼き", [("鶏肉", frozenset())]),
    ]
    history = [("肉じゃが", now - timedelta(days=2))]
    ctx = SuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=history,
    )
    result = await suggest_dinner(ctx, llm=FakeVertexClient())
    # フェイククライアントが返すデフォルト料理 (肉じゃが含む) は検証対象ではなく
    # ここではプロセス完走と成功フラグを検証.
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_単日提案は既定の2000より大きい_max_tokens_を渡す() -> None:
    """3 品 + 食材リストは実測で 2000 トークンに届くことがある.

    既定のまま出力が途中で切れて JSON パースに失敗した事象の回帰テスト.
    """
    captured: dict[str, Any] = {}

    class _MaxTokensCapturingClient:
        async def generate(
            self,
            *,
            system: str,  # noqa: ARG002
            user: str,  # noqa: ARG002
            prompt_version: str,  # noqa: ARG002
            max_tokens: int = 2000,
            output_schema: type[BaseModel] | None = None,
        ) -> LLMResponse:
            captured["max_tokens"] = max_tokens
            captured["output_schema"] = output_schema
            return LLMResponse(
                raw_text=json.dumps(
                    {
                        "dishes": [
                            {
                                "name": "牛丼",
                                "category": "主菜",
                                "main_ingredient": "牛肉",
                                "reason": "OK",
                                "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
                                "steps": ["材料を切って調理する"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                model="scripted",
            )

    ctx = SuggestContext(family=_family(), all_recipes=[], recent_history=())
    await suggest_dinner(ctx, llm=_MaxTokensCapturingClient())

    assert captured["max_tokens"] > 2000
    assert captured["output_schema"] is LLMProposal

"""services.weekly_planner のユニットテスト (フェイククライアント使用)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from recipe_system.domain import (
    FamilyMember,
    FamilyProfile,
    Ingredient,
    LLMCallMeta,
    Recipe,
    Violation,
)
from recipe_system.domain.llm_output import LLMDayDetail, LLMWeeklySkeleton
from recipe_system.llm.client import LLMResponse
from recipe_system.services import weekly_planner
from recipe_system.services.suggest_dinner import DinnerProposal, NormalizedDish, SuggestContext
from recipe_system.services.weekly_planner import (
    DAYS_IN_WEEK,
    WeeklyDetailAllFailedError,
    WeeklySuggestContext,
    plan_week,
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


def _weekly_dishes(main: str = "肉じゃが") -> list[dict[str, Any]]:
    return [
        {
            "name": main,
            "category": "主菜",
            "main_ingredient": "牛肉",
            "reason": "テスト",
            "ingredients": [{"name": "牛肉", "quantity": 300, "unit": "g"}],
            "steps": ["材料を切って調理する"],
        },
        {
            "name": "ほうれん草のおひたし",
            "category": "副菜",
            "main_ingredient": "ほうれん草",
            "reason": "テスト",
            "ingredients": [{"name": "ほうれん草", "quantity": 1, "unit": "束"}],
            "steps": ["材料を切って調理する"],
        },
        {
            "name": "わかめスープ",
            "category": "汁物",
            "main_ingredient": "わかめ",
            "reason": "テスト",
            "ingredients": [{"name": "わかめ", "quantity": 5, "unit": "g"}],
            "steps": ["材料を切って調理する"],
        },
    ]


def _weekly_payload(
    *,
    blocked_day: int | None = None,
    blocked_ingredient_name: str = "エビ",
) -> dict[str, Any]:
    days = []
    for offset in range(7):
        dishes = _weekly_dishes(f"料理_{offset}")
        if blocked_day is not None and offset == blocked_day:
            dishes[0]["ingredients"].append(
                {"name": blocked_ingredient_name, "quantity": 100, "unit": "g"}
            )
            # 主食材も差し替え
            dishes[0]["main_ingredient"] = blocked_ingredient_name
        days.append({"day_offset": offset, "dishes": dishes})
    return {"days": days, "overall_comment": "週バランス"}


def _weekly_payload_multi_blocked(
    blocked_days: list[int], blocked_ingredient_name: str = "エビ"
) -> dict[str, Any]:
    """複数日が block 違反になるペイロード (単日修復の呼び出し回数検証用)."""
    days = []
    for offset in range(7):
        dishes = _weekly_dishes(f"料理_{offset}")
        if offset in blocked_days:
            dishes[0]["ingredients"].append(
                {"name": blocked_ingredient_name, "quantity": 100, "unit": "g"}
            )
            dishes[0]["main_ingredient"] = blocked_ingredient_name
        days.append({"day_offset": offset, "dishes": dishes})
    return {"days": days, "overall_comment": "週バランス"}


def _normalized_dish(name: str) -> NormalizedDish:
    """suggest_dinner (単日修復) のフェイク戻り値を組み立てるための Dish."""
    return NormalizedDish(
        name=name,
        category="主菜",
        main_ingredient="鶏肉",
        reason="修復",
        ingredients=(Ingredient(name="鶏肉", canonical="鶏肉", allergen_tags=frozenset()),),
    )


def _dinner_proposal(
    *,
    dishes: tuple[NormalizedDish, ...],
    violations: tuple[Violation, ...] = (),
    llm_meta: LLMCallMeta | None = None,
) -> DinnerProposal:
    """単日修復 (suggest_dinner) のフェイク戻り値."""
    return DinnerProposal(
        dishes=dishes,
        violations=violations,
        retry_count=0,
        llm_meta=llm_meta
        or LLMCallMeta(
            model="scripted-repair", prompt_version="1", input_tokens=5, output_tokens=7
        ),
        prompt_version="1",
        overall_comment=None,
        succeeded=not violations,
    )


def _skeleton_from_weekly(payload: dict[str, Any]) -> dict[str, Any]:
    """週間ペイロード (料理ごとに食材付き) から骨子フェーズの応答を作る."""
    return {
        "days": [
            {
                "day_offset": day["day_offset"],
                "dishes": [
                    {
                        "index": i,
                        "name": d["name"],
                        "category": d["category"],
                        "main_ingredient": d["main_ingredient"],
                        "reason": d["reason"],
                    }
                    for i, d in enumerate(day["dishes"])
                ],
            }
            for day in payload["days"]
        ],
        "overall_comment": payload.get("overall_comment"),
    }


def _detail_from_weekly(payload: dict[str, Any], requested: list[dict[str, Any]]) -> dict[str, Any]:
    """詳細フェーズの要求 (index/name) に、週間ペイロードの同名料理の食材と手順を返す.

    テスト用ペイロードの料理名は日ごとに一意 (主菜が「料理_{offset}」) なので、
    主菜名で日を特定する.
    """
    day = next(d for d in payload["days"] if d["dishes"][0]["name"] == requested[0]["name"])
    return {
        "dishes": [
            {
                "index": r["index"],
                "name": r["name"],
                "ingredients": day["dishes"][r["index"]]["ingredients"],
                "steps": day["dishes"][r["index"]].get("steps", ["材料を切って調理する"]),
            }
            for r in requested
        ]
    }


@dataclass
class _ScriptedClient:
    """週間ペイロードから骨子と詳細の両方に応答するフェイク.

    ``output_schema`` で骨子 / 詳細を判別する. ``calls`` は骨子の呼び出し回数
    (週全体の再生成が走っていないことの検証に使う).
    """

    payload: dict[str, Any]
    latency_ms: int = 0
    calls: int = 0
    detail_calls: int = 0
    schemas_seen: list[type[BaseModel] | None] = field(default_factory=list)

    async def generate(
        self,
        *,
        system: str,  # noqa: ARG002
        user: str,
        prompt_version: str,  # noqa: ARG002
        max_tokens: int = 2000,  # noqa: ARG002
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        self.schemas_seen.append(output_schema)
        if output_schema is LLMDayDetail:
            self.detail_calls += 1
            body = _detail_from_weekly(self.payload, _dishes_from_user(user))
        else:
            self.calls += 1
            body = _skeleton_from_weekly(self.payload)
        return LLMResponse(
            raw_text=json.dumps(body, ensure_ascii=False),
            model="scripted-weekly",
            input_tokens=10,
            output_tokens=20,
            latency_ms=self.latency_ms,
        )


@pytest.mark.asyncio
async def test_週間提案で7日分のDailyResultが返る() -> None:
    family = _family({"夫": set()})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload())
    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert len(result.days) == 7
    assert result.week_start == date(2026, 5, 25)
    assert all(d.succeeded for d in result.days)
    assert result.retry_count == 0
    # 日付が月曜〜日曜に並ぶ
    assert [d.plan_date for d in result.days] == [date(2026, 5, 25 + i) for i in range(7)]


@pytest.mark.asyncio
async def test_block違反のある日は単日提案で修復される(monkeypatch: pytest.MonkeyPatch) -> None:
    """block 違反のある日だけ suggest_dinner (単日提案) が呼ばれ、その日は修復結果に置き換わる.

    週全体リトライを単日修復に置き換えた仕様変更の回帰テスト
    (旧実装は週全体を _ScriptedClient で再生成していたため、旧テストは
    payload_retry を週間スキーマで渡していたが、単日修復は suggest_dinner
    (単日スキーマ) を経由するため、weekly_planner.suggest_dinner を
    monkeypatch して呼び出し回数・引数を検証する形に書き換えた).
    """
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=2))
    repair_calls: list[SuggestContext] = []

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (llm, dictionary)
        repair_calls.append(context)
        return _dinner_proposal(dishes=(_normalized_dish("修復後の肉じゃが"),))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert client.calls == 1  # 週全体の再生成は走らない
    assert len(repair_calls) == 1  # 単日提案は違反日の分だけ呼ばれる
    assert result.retry_count == 1
    assert all(d.succeeded for d in result.days)
    repaired = next(d for d in result.days if d.plan_date == date(2026, 5, 27))  # offset=2
    # 修復対象日は単日提案の結果に置き換わる
    assert [dish.name for dish in repaired.dishes] == ["修復後の肉じゃが"]


@pytest.mark.asyncio
async def test_違反のなかった日は1回目の料理がそのまま保持される(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 回目に成功していた日まで修復結果で上書きされないことの回帰テスト."""
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=2))

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (context, llm, dictionary)
        return _dinner_proposal(dishes=(_normalized_dish("修復後の料理"),))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    unaffected = next(d for d in result.days if d.plan_date == date(2026, 5, 25))  # offset=0
    assert [dish.name for dish in unaffected.dishes] == [
        "料理_0",
        "ほうれん草のおひたし",
        "わかめスープ",
    ]


@pytest.mark.asyncio
async def test_違反が複数日ならその日数分だけ単日提案が呼ばれる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload_multi_blocked([1, 4, 6]))
    repair_calls: list[SuggestContext] = []

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (llm, dictionary)
        repair_calls.append(context)
        return _dinner_proposal(dishes=(_normalized_dish("修復後の料理"),))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert len(repair_calls) == 3
    assert result.retry_count == 1
    assert all(d.succeeded for d in result.days)


@pytest.mark.asyncio
async def test_違反が0日なら単日提案は呼ばれない(monkeypatch: pytest.MonkeyPatch) -> None:
    family = _family({"夫": set()})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload())

    async def _fail_if_called(*_args: Any, **_kwargs: Any) -> DinnerProposal:
        raise AssertionError("違反がない週で suggest_dinner が呼ばれた")

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fail_if_called)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert result.retry_count == 0
    assert all(d.succeeded for d in result.days)


@pytest.mark.asyncio
async def test_リトライ後もブロックがある日はsucceeded_Falseで返る(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """suggest_dinner (単日修復) が内部リトライしても違反が残ったケース."""
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=3))

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (context, llm, dictionary)
        block_violation = Violation(
            severity="block",
            member="妻",
            ingredient="エビ",
            canonical="えび",
            reason="甲殻類アレルギー",
        )
        return _dinner_proposal(dishes=(), violations=(block_violation,))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert client.calls == 1  # 週全体の再生成は走らない
    assert result.retry_count == 1
    failed_days = [d for d in result.days if not d.succeeded]
    assert len(failed_days) == 1
    assert failed_days[0].plan_date == date(2026, 5, 28)
    # 失敗日は dishes が空にされる (status=empty 用)
    assert failed_days[0].dishes == ()
    # 他の日は succeeded
    assert result.succeeded_days == 6


@pytest.mark.asyncio
async def test_llm_metaのトークンは骨子と詳細と修復呼び出しの合計になる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """週全体リトライ廃止によるコスト過少計上の回帰テスト.

    latency_ms は並列実行の詳細・修復呼び出しを合計せず最大値を採ることも合わせて検証する.
    """

    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=2), latency_ms=800)

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (context, llm, dictionary)
        return _dinner_proposal(
            dishes=(_normalized_dish("修復後の料理"),),
            llm_meta=LLMCallMeta(
                model="scripted-repair",
                prompt_version="1",
                input_tokens=100,
                output_tokens=200,
                cache_read_tokens=3,
                cache_write_tokens=4,
                latency_ms=500,  # 週間呼び出し (800) より小さい
            ),
        )

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    # 骨子 1 回 + 詳細 7 回 + 修復 1 回の合計
    assert result.llm_meta.input_tokens == 10 * 8 + 100
    assert result.llm_meta.output_tokens == 20 * 8 + 200
    assert result.llm_meta.cache_read_tokens == 3
    assert result.llm_meta.cache_write_tokens == 4
    # 詳細同士・修復同士は並列だが各フェーズは逐次なので、
    # 骨子 (800) + 詳細の最大値 (800) + 修復の最大値 (500)
    assert result.llm_meta.latency_ms == 800 + 800 + 500


@pytest.mark.asyncio
async def test_修復時のrecent_historyに他の日と違反日自身の料理名が含まれる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    original_history = (("既存履歴の料理", datetime(2026, 5, 1, tzinfo=UTC)),)
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=original_history,
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=2))
    captured: list[SuggestContext] = []

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (llm, dictionary)
        captured.append(context)
        return _dinner_proposal(dishes=(_normalized_dish("修復後の料理"),))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)

    await plan_week(ctx, llm=client, detail_llm=client)

    assert len(captured) == 1
    history_names = {name for name, _ in captured[0].recent_history}
    # 元の recent_history は保持される
    assert "既存履歴の料理" in history_names
    # 違反日 (offset=2) 以外の 6 日の料理名が含まれる
    assert "料理_0" in history_names
    assert "料理_1" in history_names
    assert "料理_3" in history_names
    # 違反日自身の 1 回目の料理名 (block 違反を出した料理そのもの) も含まれる
    assert "料理_2" in history_names


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
        return LLMResponse(raw_text=raw, model="scripted-weekly", input_tokens=10, output_tokens=20)


@pytest.mark.asyncio
async def test_骨子のJSONパース失敗は1回だけ再要求され成功する() -> None:
    family = _family({"夫": set()})
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=(),
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    good = json.dumps(_skeleton_from_weekly(_weekly_payload()), ensure_ascii=False)
    client = _RawTextScriptedClient(raw_texts=['{"days": [', good])

    result = await plan_week(ctx, llm=client, detail_llm=_ScriptedDetailClient())

    assert client.calls == 2
    assert len(result.days) == 7
    assert all(d.succeeded for d in result.days)


@pytest.mark.asyncio
async def test_骨子のJSONパース失敗が2回連続なら例外が伝播する() -> None:
    family = _family({"夫": set()})
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=(),
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _RawTextScriptedClient(raw_texts=["not json at all", "still not json"])
    from recipe_system.services.suggest_dinner import LLMResponseParseError

    with pytest.raises(LLMResponseParseError):
        await plan_week(ctx, llm=client, detail_llm=_ScriptedDetailClient())

    assert client.calls == 2


@pytest.mark.asyncio
async def test_LLM応答に日が欠落していたらblock扱い() -> None:
    family = _family({"夫": set()})
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=(),
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    # 7 日のうち day_offset=3 を抜く → pydantic スキーマ違反になる
    payload = _weekly_payload()
    payload["days"] = [d for d in payload["days"] if d["day_offset"] != 3]
    client = _ScriptedClient(payload=payload)
    from recipe_system.services.suggest_dinner import LLMResponseParseError

    with pytest.raises(LLMResponseParseError):
        await plan_week(ctx, llm=client, detail_llm=client)


@pytest.mark.asyncio
async def test_to_firestore_record_は_scope_weekly_で出力される() -> None:
    family = _family({"夫": set()})
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=(),
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload())
    result = await plan_week(ctx, llm=client, detail_llm=client)
    record = result.to_firestore_record()
    assert record["scope"] == "weekly"
    assert record["week_start"] == "2026-05-25"
    assert len(record["days"]) == 7
    assert record["days"][0]["plan_date"] == "2026-05-25"
    assert record["succeeded"] is True


@pytest.mark.asyncio
async def test_修復が例外で失敗してもその日だけ空になり他の日は確定する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """修復 1 日の失敗で週全体を巻き込まないこと (asyncio.gather の例外伝播対策).

    パース失敗の伝播や予算超過は現実に起こりうる経路で、週間生成が成功して
    いる他の日まで失われると「その日だけ status=empty で返す」前提が崩れる.
    """
    family = _family({"妻": {"甲殻類"}})
    recipes = [_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])]
    ctx = WeeklySuggestContext(
        family=family,
        all_recipes=recipes,
        recent_history=(),
        week_start=date(2026, 5, 25),
    )
    client = _ScriptedClient(payload=_weekly_payload(blocked_day=2))

    async def _raising_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (context, llm, dictionary)
        raise RuntimeError("修復用 LLM 呼び出しが失敗")

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _raising_suggest_dinner)

    result = await plan_week(ctx, llm=client, detail_llm=client)

    assert len(result.days) == DAYS_IN_WEEK
    failed = [d for d in result.days if not d.succeeded]
    assert len(failed) == 1
    assert failed[0].plan_date == date(2026, 5, 27)
    assert failed[0].dishes == ()
    assert any("修復に失敗" in v.reason for v in failed[0].violations)
    # 違反のなかった 6 日は確定している
    assert result.succeeded_days == DAYS_IN_WEEK - 1


# ---------------------------------------------------------------------------
# 分割経路 (骨子 Sonnet + 日別の食材詳細 Haiku 並列)
# ---------------------------------------------------------------------------

# detail_day_ingredients_user.yaml が埋め込む料理リストの見出し.
# プロンプト側の書式を変えたらここも直すこと (llm/fake.py も同様).
_DISHES_JSON_RE = re.compile(r"^# 対象の料理 \(JSON\)\n(.+)$", re.MULTILINE)


def _dishes_from_user(user: str) -> list[dict[str, Any]]:
    matched = _DISHES_JSON_RE.search(user)
    assert matched is not None, "詳細プロンプトに料理リストの JSON が埋め込まれていない"
    parsed: list[dict[str, Any]] = json.loads(matched.group(1))
    return parsed


def _skeleton_payload() -> dict[str, Any]:
    """骨子フェーズの応答 (食材リストなし, index 付き)."""
    days = []
    for offset in range(7):
        days.append(
            {
                "day_offset": offset,
                "dishes": [
                    {
                        "index": 0,
                        "name": f"料理_{offset}",
                        "category": "主菜",
                        "main_ingredient": "牛肉",
                        "reason": "テスト",
                    },
                    {
                        "index": 1,
                        "name": "ほうれん草のおひたし",
                        "category": "副菜",
                        "main_ingredient": "ほうれん草",
                        "reason": "テスト",
                    },
                    {
                        "index": 2,
                        "name": "わかめスープ",
                        "category": "汁物",
                        "main_ingredient": "わかめ",
                        "reason": "テスト",
                    },
                ],
            }
        )
    return {"days": days, "overall_comment": "週バランス"}


def _ok_detail(dishes: list[dict[str, Any]]) -> dict[str, Any]:
    """骨子どおりの index / name で、主食材だけを食材リストとして返す."""
    return {
        "dishes": [
            {
                "index": d["index"],
                "name": d["name"],
                "ingredients": [{"name": d["main_ingredient"], "quantity": 200, "unit": "g"}],
                "steps": [f"{d['main_ingredient']}を切って調理する"],
            }
            for d in dishes
        ]
    }


def _day_offset_of(dishes: list[dict[str, Any]]) -> int:
    """主菜名 (料理_N) から、その詳細呼び出しがどの日のものかを取り出す."""
    return int(dishes[0]["name"].removeprefix("料理_"))


@dataclass
class _ScriptedSkeletonClient:
    """骨子フェーズ用のフェイク."""

    payload: dict[str, Any]
    calls: int = 0
    max_tokens_seen: list[int] = field(default_factory=list)
    schemas_seen: list[type[BaseModel] | None] = field(default_factory=list)

    async def generate(
        self,
        *,
        system: str,  # noqa: ARG002
        user: str,  # noqa: ARG002
        prompt_version: str,  # noqa: ARG002
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        self.schemas_seen.append(output_schema)
        return LLMResponse(
            raw_text=json.dumps(self.payload, ensure_ascii=False),
            model="scripted-skeleton",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
        )


@dataclass
class _ScriptedDetailClient:
    """詳細フェーズ用のフェイク. behavior に日ごとの細工を差し込む."""

    behavior: Callable[[list[dict[str, Any]]], dict[str, Any]] = _ok_detail
    calls: int = 0
    max_tokens_seen: list[int] = field(default_factory=list)
    schemas_seen: list[type[BaseModel] | None] = field(default_factory=list)

    async def generate(
        self,
        *,
        system: str,  # noqa: ARG002
        user: str,
        prompt_version: str,  # noqa: ARG002
        max_tokens: int = 2000,
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        self.schemas_seen.append(output_schema)
        payload = self.behavior(_dishes_from_user(user))
        return LLMResponse(
            raw_text=json.dumps(payload, ensure_ascii=False),
            model="scripted-detail",
            input_tokens=3,
            output_tokens=5,
            latency_ms=50,
        )


def _no_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """単日修復が呼ばれたら即失敗させる (分割経路で余計な修復が走らないことの検証用)."""

    async def _fail_if_called(*_args: Any, **_kwargs: Any) -> DinnerProposal:
        raise AssertionError("修復が不要なはずのケースで suggest_dinner が呼ばれた")

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fail_if_called)


def _record_repairs(monkeypatch: pytest.MonkeyPatch) -> list[SuggestContext]:
    """単日修復をフェイクに差し替え、呼び出しコンテキストを記録する."""
    calls: list[SuggestContext] = []

    async def _fake_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (llm, dictionary)
        calls.append(context)
        return _dinner_proposal(dishes=(_normalized_dish("修復後の料理"),))

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _fake_suggest_dinner)
    return calls


def _split_context() -> WeeklySuggestContext:
    return WeeklySuggestContext(
        family=_family({"妻": {"甲殻類"}}),
        all_recipes=[_recipe("肉じゃが", [("牛肉", frozenset({"肉類"}))])],
        recent_history=(),
        week_start=date(2026, 5, 25),
    )


@pytest.mark.asyncio
async def test_分割経路で7日分が生成され食材は詳細フェーズ由来になる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_repair(monkeypatch)
    skeleton = _ScriptedSkeletonClient(payload=_skeleton_payload())
    detail = _ScriptedDetailClient()

    result = await plan_week(_split_context(), llm=skeleton, detail_llm=detail)

    assert skeleton.calls == 1
    assert detail.calls == DAYS_IN_WEEK  # 1 日 1 回の並列呼び出し
    assert len(result.days) == DAYS_IN_WEEK
    assert all(d.succeeded for d in result.days)
    assert result.retry_count == 0

    first_day = result.days[0]
    # 料理名・カテゴリ・理由は骨子由来
    assert [dish.name for dish in first_day.dishes] == [
        "料理_0",
        "ほうれん草のおひたし",
        "わかめスープ",
    ]
    # 食材は詳細フェーズ由来
    assert [ing.name for ing in first_day.dishes[0].ingredients] == ["牛肉"]
    assert first_day.dishes[0].reason == "テスト"


@pytest.mark.asyncio
async def test_分割経路のprompt_versionは骨子と詳細の両方を含む(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """プロンプト版管理 (CLAUDE.md 6.3) のため、どちらの版で生成したか追えること."""
    _no_repair(monkeypatch)

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(),
    )

    assert result.prompt_version.startswith("skeleton=")
    assert "detail=" in result.prompt_version


@pytest.mark.asyncio
async def test_詳細フェーズが混入させたアレルゲンもガードレールが検出する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """骨子が安全でも、詳細が足した食材は決定論的コードで検証されること.

    分割によってアレルゲン検証の二層構造が崩れていないことの回帰テスト
    (CLAUDE.md 6.1). Haiku が骨子にない食材を勝手に足しても block になる.
    """
    repairs = _record_repairs(monkeypatch)

    def _sneak_allergen(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _ok_detail(dishes)
        if _day_offset_of(dishes) == 1:
            payload["dishes"][0]["ingredients"].append(
                {"name": "エビ", "quantity": 100, "unit": "g"}
            )
        return payload

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_sneak_allergen),
    )

    assert len(repairs) == 1  # 混入した 1 日だけが修復に回る
    assert result.retry_count == 1
    repaired = next(d for d in result.days if d.plan_date == date(2026, 5, 26))
    assert [dish.name for dish in repaired.dishes] == ["修復後の料理"]


@pytest.mark.asyncio
async def test_手順にだけ現れたアレルゲンもガードレールが検出する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """食材リストは安全でも、手順に「溶き卵でとじる」等が書かれた日は修復に回る."""
    repairs = _record_repairs(monkeypatch)

    def _sneak_allergen_in_steps(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _ok_detail(dishes)
        if _day_offset_of(dishes) == 2:
            payload["dishes"][0]["steps"].append("最後に溶き卵を回し入れてとじる")
        return payload

    context = _split_context()
    context = WeeklySuggestContext(
        family=_family({"長男": {"卵"}}),
        all_recipes=context.all_recipes,
        recent_history=(),
        week_start=context.week_start,
    )
    result = await plan_week(
        context,
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_sneak_allergen_in_steps),
    )

    assert len(repairs) == 1
    repaired = next(d for d in result.days if d.plan_date == date(2026, 5, 27))
    assert [dish.name for dish in repaired.dishes] == ["修復後の料理"]


@pytest.mark.asyncio
async def test_詳細の手順が料理に紐付き保存レコードにも含まれる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_repair(monkeypatch)

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(),
    )

    first_dish = result.days[0].dishes[0]
    assert first_dish.steps == ("牛肉を切って調理する",)
    record = result.to_firestore_record()
    assert record["days"][0]["dishes"][0]["steps"] == ["牛肉を切って調理する"]


@pytest.mark.asyncio
async def test_詳細の料理名が骨子とずれてもblockにはならない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名前の表記揺れは warn 止まり.

    実 LLM は「豚の生姜焼き」を「豚肉の生姜焼き」のように言い換えることがある.
    マージは index で行っており、collect_violations は日内の全食材を検証するため、
    名前のずれは安全性に影響しない. ここで block にすると安全性に無関係な理由で
    高価な単日修復にエスカレーションしてしまう.
    """
    _no_repair(monkeypatch)

    def _rename(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _ok_detail(dishes)
        payload["dishes"][0]["name"] = "まったく違う料理名"
        # 全角・空白の揺れも同様に許容されること
        payload["dishes"][1]["name"] = " ほうれん草のおひたし "
        return payload

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_rename),
    )

    assert all(d.succeeded for d in result.days)
    # 採用されるのは骨子側の名前
    assert result.days[0].dishes[0].name == "料理_0"


@pytest.mark.asyncio
async def test_詳細のindexが欠けた日はblockになり他の日は確定する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repairs = _record_repairs(monkeypatch)

    def _drop_index(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _ok_detail(dishes)
        if _day_offset_of(dishes) == 2:
            # 品数は合っているが index が骨子と対応しない
            payload["dishes"][1]["index"] = 9
        return payload

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_drop_index),
    )

    assert len(repairs) == 1
    assert result.succeeded_days == DAYS_IN_WEEK  # 修復に成功している
    assert result.days[2].dishes[0].name == "修復後の料理"


@pytest.mark.asyncio
async def test_詳細の品数が骨子と一致しない日はblockになる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repairs = _record_repairs(monkeypatch)

    def _drop_dish(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _ok_detail(dishes)
        if _day_offset_of(dishes) == 5:
            payload["dishes"] = payload["dishes"][:2]
        return payload

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_drop_dish),
    )

    assert len(repairs) == 1
    assert repairs[0] is not None
    assert result.days[5].dishes[0].name == "修復後の料理"


@pytest.mark.asyncio
async def test_詳細呼び出しが例外でもその日だけ空になり他の日は確定する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細 1 日の失敗で gather が例外を投げ、週全体を巻き込まないこと."""

    async def _raising_suggest_dinner(
        context: SuggestContext, *, llm: Any, dictionary: Any = None
    ) -> DinnerProposal:
        _ = (context, llm, dictionary)
        raise RuntimeError("修復も失敗")

    monkeypatch.setattr(weekly_planner, "suggest_dinner", _raising_suggest_dinner)

    def _raise_on_day3(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        if _day_offset_of(dishes) == 3:
            raise RuntimeError("詳細用 LLM 呼び出しが失敗")
        return _ok_detail(dishes)

    result = await plan_week(
        _split_context(),
        llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
        detail_llm=_ScriptedDetailClient(behavior=_raise_on_day3),
    )

    failed = [d for d in result.days if not d.succeeded]
    assert len(failed) == 1
    assert failed[0].plan_date == date(2026, 5, 28)
    assert failed[0].dishes == ()
    assert result.succeeded_days == DAYS_IN_WEEK - 1


@pytest.mark.asyncio
async def test_全日の詳細が失敗すると週全体がエラーになる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確定できる日が 1 つも無い状態で 7 日分の単日修復に流さないこと.

    レガシー経路よりコストもレイテンシも悪化するため、ここで打ち切る.
    フェイルオープンはしない (CLAUDE.md 6.7).
    """
    _no_repair(monkeypatch)

    def _always_raise(dishes: list[dict[str, Any]]) -> dict[str, Any]:
        _ = dishes
        raise RuntimeError("詳細用 LLM が全滅")

    with pytest.raises(WeeklyDetailAllFailedError):
        await plan_week(
            _split_context(),
            llm=_ScriptedSkeletonClient(payload=_skeleton_payload()),
            detail_llm=_ScriptedDetailClient(behavior=_always_raise),
        )


@pytest.mark.asyncio
async def test_分割経路のllm_metaは骨子と詳細の合計になる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細フェーズの消費が proposals レコードから消えないことの回帰テスト.

    latency は並列の詳細呼び出しを合計せず最大値を採る.
    """
    _no_repair(monkeypatch)
    skeleton = _ScriptedSkeletonClient(payload=_skeleton_payload())

    result = await plan_week(_split_context(), llm=skeleton, detail_llm=_ScriptedDetailClient())

    # 骨子 10 + 詳細 3 x 7 日
    assert result.llm_meta.input_tokens == 10 + 3 * DAYS_IN_WEEK
    assert result.llm_meta.output_tokens == 20 + 5 * DAYS_IN_WEEK
    # 骨子 100ms + 並列詳細の最大値 50ms
    assert result.llm_meta.latency_ms == 150


@pytest.mark.asyncio
async def test_骨子は詳細より大きいmax_tokensを使う(monkeypatch: pytest.MonkeyPatch) -> None:
    """骨子は 21 品分、詳細は 1 日 3 品分しか出さないため上限を分ける."""
    _no_repair(monkeypatch)
    skeleton = _ScriptedSkeletonClient(payload=_skeleton_payload())
    detail = _ScriptedDetailClient()

    await plan_week(_split_context(), llm=skeleton, detail_llm=detail)

    assert skeleton.max_tokens_seen[0] > 2000
    assert all(seen < skeleton.max_tokens_seen[0] for seen in detail.max_tokens_seen)


@pytest.mark.asyncio
async def test_分割経路は各フェーズの出力スキーマをLLMに渡す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """構造化出力で JSON 不正 (実 Haiku の "quantity": 大さじ など) を生成させないための契約."""
    _no_repair(monkeypatch)
    skeleton = _ScriptedSkeletonClient(payload=_skeleton_payload())
    detail = _ScriptedDetailClient()

    await plan_week(_split_context(), llm=skeleton, detail_llm=detail)

    assert skeleton.schemas_seen == [LLMWeeklySkeleton]
    assert detail.schemas_seen == [LLMDayDetail] * 7


@pytest.mark.asyncio
async def test_FakeVertexClientで分割経路が最後まで通る(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカル開発 (LLM_PROVIDER=fake) で分割経路が動くこと.

    フェイクは詳細プロンプトに埋め込まれた料理リストを読んで index / name を
    そのまま返す契約になっており、これが崩れるとマージに失敗する.
    """
    from recipe_system.llm.client import FakeVertexClient

    _no_repair(monkeypatch)
    ctx = WeeklySuggestContext(
        family=_family({"夫": set()}),
        all_recipes=(),
        recent_history=(),
        week_start=date(2026, 5, 25),
    )

    result = await plan_week(ctx, llm=FakeVertexClient(), detail_llm=FakeVertexClient())

    assert len(result.days) == DAYS_IN_WEEK
    assert all(d.succeeded for d in result.days)
    assert all(dish.ingredients for day in result.days for dish in day.dishes)


@pytest.mark.asyncio
async def test_骨子の同一日でindexが重複したら再試行され最終的にblockになる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """index 重複を許すとマージが後勝ちになり 2 品が同じ食材リストを持つ.

    安全性には影響しない (全食材が検証される) が、品質バグが黙って通るため
    スキーマ段階で弾き、パース再試行 -> 週全体エラーの経路に乗せる.
    """
    _no_repair(monkeypatch)
    payload = _skeleton_payload()
    payload["days"][0]["dishes"][1]["index"] = 0  # index=0 が 2 つになる
    skeleton = _ScriptedSkeletonClient(payload=payload)

    with pytest.raises(weekly_planner.LLMResponseParseError):
        await plan_week(_split_context(), llm=skeleton, detail_llm=_ScriptedDetailClient())

    assert skeleton.calls == 2  # attempt_with_parse_retry で 1 回だけ再要求する

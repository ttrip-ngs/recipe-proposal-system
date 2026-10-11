"""`recipe_system.llm.fake.FakeVertexClient` の応答切替 (単日 / 骨子 / 詳細) のテスト.

骨子・詳細の判定はプロンプト文字列に含まれる語句に依存するヒューリスティックのため、
プロンプト YAML の文言が変わると静かに壊れうる. 判定の入出力を単体で固定しておく.
"""

from __future__ import annotations

import asyncio
import itertools
import json

from recipe_system.llm.client import FakeVertexClient


def test_単日プロンプトなら_dishes_を含む応答() -> None:
    client = FakeVertexClient()
    response = asyncio.run(
        client.generate(system="通常の夕食提案プロンプト", user="u", prompt_version="v1")
    )
    payload = json.loads(response.raw_text)
    assert "dishes" in payload
    assert "days" not in payload


def test_preset_指定時はヒューリスティックより優先される() -> None:
    client = FakeVertexClient(preset={"dishes": [], "overall_comment": "empty"})
    response = asyncio.run(
        client.generate(
            system="week_start_label を含む週間プロンプトでも",
            user="u",
            prompt_version="v1",
        )
    )
    payload = json.loads(response.raw_text)
    assert payload == {"dishes": [], "overall_comment": "empty"}


def test_骨子プロンプトなら食材リストなしの応答() -> None:
    """分割経路の骨子フェイクは ingredients を持たず index を持つ."""
    client = FakeVertexClient()
    response = asyncio.run(
        client.generate(
            system=(
                "7 日間の献立です。このフェーズでは料理の選定のみを行い、食材リストは出力しません。"
            ),
            user="u",
            prompt_version="v1",
        )
    )
    payload = json.loads(response.raw_text)
    assert len(payload["days"]) == 7
    first_dish = payload["days"][0]["dishes"][0]
    assert "ingredients" not in first_dish
    assert first_dish["index"] == 0


def test_詳細プロンプトなら入力の_index_と_name_をそのまま返す() -> None:
    """マージは index で行うため、フェイクも入力どおりの index を返す必要がある."""
    dishes = [
        {"index": 0, "name": "鶏の照り焼き", "category": "主菜", "main_ingredient": "鶏肉"},
        {"index": 1, "name": "きんぴらごぼう", "category": "副菜", "main_ingredient": "ごぼう"},
    ]
    user = "2026年5月25日 の献立です。\n\n# 対象の料理 (JSON)\n" + json.dumps(
        dishes, ensure_ascii=False
    )

    client = FakeVertexClient()
    response = asyncio.run(client.generate(system="食材リスト", user=user, prompt_version="v1"))
    payload = json.loads(response.raw_text)

    assert [d["index"] for d in payload["dishes"]] == [0, 1]
    assert [d["name"] for d in payload["dishes"]] == ["鶏の照り焼き", "きんぴらごぼう"]
    # ingredients は min_length=1 を満たす必要がある
    assert all(d["ingredients"] for d in payload["dishes"])
    assert payload["dishes"][0]["ingredients"][0]["name"] == "鶏肉"


def _single_day(system: str, user: str = "u") -> list[dict[str, object]]:
    response = asyncio.run(
        FakeVertexClient().generate(system=system, user=user, prompt_version="v1")
    )
    dishes: list[dict[str, object]] = json.loads(response.raw_text)["dishes"]
    return dishes


def test_単日は直近履歴にある料理を避ける() -> None:
    """固定献立を返すと直近重複のゴールデンケース (G101) が原理的に通らない回帰の防止."""
    recent = ["肉じゃが", "豚の生姜焼き", "鶏の照り焼き", "ほうれん草のおひたし"]
    dishes = _single_day("前置き\n直近 7 日に作った料理: " + " / ".join(recent) + "\n後続")

    assert [d["category"] for d in dishes] == ["主菜", "副菜", "汁物"]
    assert not {d["name"] for d in dishes} & set(recent)


def test_単日は直近履歴でない候補レシピを優先する() -> None:
    user = (
        "# 候補レシピ (事前フィルタ済み)\n"
        "- 筑前煮 (主菜 / 主食材: 鶏肉)\n"
        "- 白菜の浅漬け (副菜 / 主食材: 白菜)\n"
    )
    dishes = _single_day("直近 7 日に作った料理: 筑前煮", user)

    names = [d["name"] for d in dishes]
    assert "筑前煮" not in names
    assert names[1] == "白菜の浅漬け"
    assert dishes[1]["ingredients"] == [{"name": "白菜", "quantity": 200, "unit": "g"}]


def test_骨子は直近履歴を避け連続日の主食材を変える() -> None:
    recent = ["肉じゃが", "鮭の塩焼き", "麻婆豆腐", "豆腐とわかめの味噌汁"]
    system = "食材リストは出力しません。\n直近 14 日に作った料理: " + " / ".join(recent) + "\n"
    response = asyncio.run(
        FakeVertexClient().generate(system=system, user="u", prompt_version="v1")
    )
    days = json.loads(response.raw_text)["days"]

    assert len(days) == 7
    names = {dish["name"] for day in days for dish in day["dishes"]}
    assert not names & set(recent)
    mains = [day["dishes"][0]["main_ingredient"] for day in days]
    assert all(a != b for a, b in itertools.pairwise(mains))


def test_既定の献立の食材はすべて辞書に登録済み() -> None:
    """未登録食材があると評価の unknown_ingredient_rate がフェイク都合で上がる."""
    from recipe_system.guardrails.dictionary_loader import default_dictionary
    from recipe_system.llm.fake import _MAIN_POOL, _SIDE_POOL, _SOUP_POOL

    dictionary = default_dictionary()
    unknown = [
        name
        for dish in (*_MAIN_POOL, *_SIDE_POOL, *_SOUP_POOL)
        for name, _, _ in dish.ingredients
        if not dictionary.is_known(name)
    ]
    assert unknown == []

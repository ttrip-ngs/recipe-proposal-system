"""`recipe_system.llm.fake.FakeVertexClient` の応答切替 (単日 / 骨子 / 詳細) のテスト.

骨子・詳細の判定はプロンプト文字列に含まれる語句に依存するヒューリスティックのため、
プロンプト YAML の文言が変わると静かに壊れうる. 判定の入出力を単体で固定しておく.
"""

from __future__ import annotations

import asyncio
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

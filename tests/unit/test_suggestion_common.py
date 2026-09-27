"""`recipe_system.services.suggestion_common` の LLM 出力パースと正規化のテスト."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from recipe_system.domain import LLMDish, LLMIngredient
from recipe_system.guardrails.dictionary_loader import load_dictionary
from recipe_system.services import suggestion_common
from recipe_system.services.suggest_dinner import NormalizedDish
from recipe_system.services.suggestion_common import (
    LLMResponseParseError,
    normalize_dish,
    parse_llm_json,
)


class _Sample(BaseModel):
    dish: str


def _parse(raw: str) -> _Sample:
    return parse_llm_json(raw, model=_Sample, schema_error_label="スキーマ検証失敗")


def test_フェンスなしの_JSON_はそのままパースできる() -> None:
    assert _parse('{"dish": "肉じゃが"}').dish == "肉じゃが"


def test_json_指定のコードフェンスを剥がしてパースできる() -> None:
    """Claude は system プロンプトで JSON のみを求めてもフェンスで包むことが多い."""
    assert _parse('```json\n{"dish": "鮭の塩焼き"}\n```').dish == "鮭の塩焼き"


def test_言語指定なしのコードフェンスも剥がせる() -> None:
    assert _parse('```\n{"dish": "肉じゃが"}\n```').dish == "肉じゃが"


def test_フェンス前後の空白や改行があっても剥がせる() -> None:
    assert _parse('\n  ```json\n{"dish": "牛丼"}\n```  \n').dish == "牛丼"


def test_複数行の_JSON_をフェンスごと剥がせる() -> None:
    raw = '```json\n{\n  "dish": "肉じゃが"\n}\n```'
    assert _parse(raw).dish == "肉じゃが"


def test_フェンス内が不正_JSON_なら_LLMResponseParseError() -> None:
    with pytest.raises(LLMResponseParseError):
        _parse("```json\nnot json at all\n```")


def test_フェンスなしの不正_JSON_も_LLMResponseParseError() -> None:
    with pytest.raises(LLMResponseParseError):
        _parse("not json at all")


def test_スキーマ不一致は_LLMResponseParseError() -> None:
    """JSON デコードは通るがモデル検証で落ちるケース."""
    with pytest.raises(LLMResponseParseError):
        _parse('```json\n{"unexpected": 1}\n```')


def _dish(*ingredient_names: str) -> LLMDish:
    return LLMDish(
        name="テスト料理",
        category="主菜",
        main_ingredient=ingredient_names[0],
        reason="テスト",
        ingredients=tuple(LLMIngredient(name=n) for n in ingredient_names),
        steps=("焼く",),
    )


def _capture_warnings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """structlog は既定で stdlib logging を経由しないため caplog でなく直接モックする."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        suggestion_common.logger,
        "warning",
        lambda event, **kwargs: calls.append({"event": event, **kwargs}),
    )
    return calls


def test_未知食材は_warn_ログに記録し提案は正規化して返す(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_warnings(monkeypatch)
    dictionary = load_dictionary()

    dish = normalize_dish(
        _dish("むきえび", "サルティンボッカ用素材", "謎の香草"),
        dictionary,
        normalized_dish_cls=NormalizedDish,
    )

    assert [i.canonical for i in dish.ingredients] == ["エビ", "サルティンボッカ用素材", "謎の香草"]
    assert calls == [
        {
            "event": "guard.unknown_ingredient",
            "unknown_ingredient": True,
            "dish": "テスト料理",
            "ingredients": ["サルティンボッカ用素材", "謎の香草"],
            "aliases_version": dictionary.aliases_version,
        }
    ]


def test_全食材が辞書にあれば_warn_ログを出さない(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_warnings(monkeypatch)

    normalize_dish(
        _dish("むきえび", "玉ねぎ"), load_dictionary(), normalized_dish_cls=NormalizedDish
    )

    assert calls == []

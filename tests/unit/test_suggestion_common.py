"""`recipe_system.services.suggestion_common` の LLM 出力パースのテスト."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from recipe_system.services.suggestion_common import LLMResponseParseError, parse_llm_json


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

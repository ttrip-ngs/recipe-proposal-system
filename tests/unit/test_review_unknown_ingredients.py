"""scripts/review_unknown_ingredients.py の集計・振り分け・レポートのテスト (API は呼ばない)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from recipe_system.guardrails.dictionary_loader import load_dictionary

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review_unknown_ingredients.py"


@pytest.fixture(scope="module")
def review() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_unknown_ingredients", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass の型解決に必要
    spec.loader.exec_module(module)
    return module


def test_gcloud_の_JSON_から現在も未知の食材だけを数える(review: ModuleType) -> None:
    raw = json.dumps(
        [
            {
                "jsonPayload": {
                    "event": "guard.unknown_ingredient",
                    "ingredients": ["ごぼう", "溶き卵"],
                }
            },
            {"jsonPayload": {"event": "guard.unknown_ingredient", "ingredients": ["ゴボウ", "卵"]}},
        ]
    )
    counts = review.collect_unknown_names(raw, load_dictionary())
    # 卵は辞書にあるので除く. ゴボウは照合キーで ごぼう にまとめる
    assert counts == {"ごぼう": 2, "溶き卵": 1}


def test_1行1食材名のテキストも読める(review: ModuleType) -> None:
    counts = review.collect_unknown_names("こんにゃく\n\nこんにゃく\nたまご\n", load_dictionary())
    assert counts == {"こんにゃく": 2}


def _judgement(review: ModuleType, **overrides: object) -> object:
    base = {
        "name": "x",
        "count": 1,
        "canonical": review.NONE,
        "confidence": 0.9,
        "known": 0.1,
        "allergens": {"卵": 0.1, "小麦": 0.1},
    }
    return review.Judgement(**(base | overrides))


def test_アレルゲンの疑いは他の判定より優先して振り分ける(review: ModuleType) -> None:
    j = _judgement(review, canonical="小麦", known=0.95, allergens={"卵": 0.8, "小麦": 0.9})
    assert j.route == "allergen"
    assert j.allergen_hits == ["卵", "小麦"]


@pytest.mark.parametrize(
    ("canonical", "known", "route"),
    [
        ("大豆", 0.9, "alias"),
        ("該当なし", 0.9, "review"),  # Noul は辞書語らしいが Choice が該当なし: 判断が割れた
        ("大豆", 0.5, "review"),
        ("該当なし", 0.1, "unrelated"),
    ],
)
def test_アレルゲンが無ければ辞書語らしさで振り分ける(
    review: ModuleType, canonical: str, known: float, route: str
) -> None:
    assert _judgement(review, canonical=canonical, known=known).route == route


def test_質問にすべてのアレルゲングループと該当なしを含める(review: ModuleType) -> None:
    dictionary = load_dictionary()
    questions = review.build_questions(dictionary)
    assert review.NONE in questions["canon"].criteria
    groups = {k.removeprefix("allergen:") for k in questions if k.startswith("allergen:")}
    assert groups == set(dictionary.allergen_group_members)


def test_レポートは振り分け先ごとに回数順で並べる(review: ModuleType) -> None:
    judgements = [
        _judgement(review, name="ごぼう", count=3),
        _judgement(review, name="溶き卵", count=1, allergens={"卵": 0.97}),
        _judgement(review, name="れんこん", count=5),
    ]
    report = review.render_report(judgements, load_dictionary())
    assert report.index("アレルゲンを含む疑い") < report.index("辞書と無関係")
    assert "| 溶き卵 | 1 | 該当なし | 0.90 | 0.10 | 卵 0.97 |" in report
    assert report.index("| れんこん |") < report.index("| ごぼう |")

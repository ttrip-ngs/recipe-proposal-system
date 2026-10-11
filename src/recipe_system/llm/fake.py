"""Vertex AI を呼ばずに決定論的な応答を返すフェイク LLM クライアント.

ローカル開発・単体テスト・UI プレビュー・CI のゴールデン評価用. レスポンスは
LLMProposal / LLMWeeklySkeleton / LLMDayDetail スキーマに適合する JSON 文字列を
返す. `USE_FAKE_LLM=true` (または `LLM_PROVIDER=fake`) で選択される.

実 LLM と同じく、レンダリング済みプロンプトに埋め込まれた直近履歴は避け、
候補レシピ (事前フィルタ済み) があれば優先する. 固定献立を返すと、直近履歴と
重複しないことを検査するゴールデンケース (G101 など) が原理的に通らないため.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from recipe_system.llm.base import LLMResponse

if TYPE_CHECKING:
    from pydantic import BaseModel
from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)


class FakeVertexClient:
    MODEL_NAME = "fake-sonnet"

    def __init__(self, preset: dict[str, Any] | None = None) -> None:
        self._preset = preset

    async def generate(
        self,
        *,
        system: str,
        user: str,
        prompt_version: str,
        max_tokens: int = 2000,  # noqa: ARG002
        output_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        logger.info(
            "fake_llm.generate",
            prompt_version=prompt_version,
            system_len=len(system),
            user_len=len(user),
            output_schema=output_schema.__name__ if output_schema else None,
        )
        if self._preset is not None:
            payload = self._preset
        elif _is_detail_prompt(user):
            payload = _detail_fake_response(user)
        elif _is_skeleton_prompt(system):
            payload = _skeleton_fake_response(system)
        else:
            payload = _single_day_fake_response(system, user)
        raw = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            raw_text=raw,
            model=self.MODEL_NAME,
            input_tokens=0,
            output_tokens=len(raw),
            latency_ms=0,
        )


# 骨子プロンプト (plan_weekly_skeleton_system.yaml) 固有の文言.
_SKELETON_MARKER = "食材リストは出力しません"

# 詳細プロンプト (detail_day_ingredients_user.yaml) の料理リスト見出し.
# 直後の 1 行が ensure_ascii=False の JSON 配列になっている契約.
_DETAIL_DISHES_RE = re.compile(r"^# 対象の料理 \(JSON\)\n(.+)$", re.MULTILINE)


def _is_skeleton_prompt(system: str) -> bool:
    """分割経路の骨子プロンプトかどうかを判定する."""
    return _SKELETON_MARKER in system


def _is_detail_prompt(user: str) -> bool:
    """分割経路の食材詳細プロンプトかどうかを判定する."""
    return _DETAIL_DISHES_RE.search(user) is not None


def _detail_fake_response(user: str) -> dict[str, Any]:
    """user プロンプトに埋め込まれた料理リストから、そのままの index/name で食材を返す.

    骨子と詳細のマージは index で行うため、フェイクでも入力どおりの index を
    返さないと weekly_planner 側でマージ失敗になる. 依存している
    detail_day_ingredients_user.yaml の書式を変えるときはここも直すこと.
    """
    matched = _DETAIL_DISHES_RE.search(user)
    if matched is None:  # pragma: no cover - _is_detail_prompt で判定済み
        raise ValueError("フェイク詳細応答: 料理リストの JSON が見つかりません")
    dishes = json.loads(matched.group(1))
    return {
        "dishes": [
            {
                "index": dish["index"],
                "name": dish["name"],
                "ingredients": [
                    {"name": dish["main_ingredient"], "quantity": 200, "unit": "g"},
                ],
                "steps": [f"(fake) {dish['main_ingredient']}を切る", "(fake) 加熱して味を調える"],
            }
            for dish in dishes
        ]
    }


# system プロンプト (suggest_dinner_system / plan_weekly_skeleton_system) の直近履歴行.
# 料理名は " / " 区切り.
_RECENT_RE = re.compile(r"^直近 \d+ 日に作った料理: (.+)$", re.MULTILINE)

# user プロンプト (suggest_dinner_user) の候補レシピ行.
_CANDIDATE_RE = re.compile(
    r"^- (?P<name>.+) \((?P<category>主菜|副菜|汁物) / 主食材: (?P<main>.+)\)$", re.MULTILINE
)


@dataclass(frozen=True)
class _FakeDish:
    name: str
    category: str
    main_ingredient: str
    ingredients: tuple[tuple[str, float, str], ...]


# 食材は辞書 (guardrails/dictionaries) に登録済みのものだけを使う. 未登録の食材を
# 混ぜると評価の unknown_ingredient_rate がフェイク都合で 0 にならなくなる.
# ゴールデンセットのアレルゲン (卵・甲殻類・ナッツ・落花生) も含めない.
# 主菜は先頭から順に使うため、隣り合う料理の主食材を変えておく (週間の連続日重複回避).
_MAIN_POOL: tuple[_FakeDish, ...] = (
    _FakeDish(
        "肉じゃが",
        "主菜",
        "牛肉",
        (("牛肉", 300, "g"), ("じゃがいも", 4, "個"), ("たまねぎ", 1, "個"), ("にんじん", 1, "本")),
    ),
    _FakeDish("豚の生姜焼き", "主菜", "豚肉", (("豚肉", 300, "g"), ("たまねぎ", 1, "個"))),
    _FakeDish("鶏の照り焼き", "主菜", "鶏肉", (("鶏肉", 300, "g"), ("醤油", 2, "大さじ"))),
    _FakeDish("鮭の塩焼き", "主菜", "鮭", (("鮭", 3, "切れ"),)),
    _FakeDish("麻婆豆腐", "主菜", "豆腐", (("豆腐", 300, "g"), ("豚肉", 100, "g"))),
    _FakeDish("さばの味噌煮", "主菜", "さば", (("さば", 3, "切れ"), ("味噌", 2, "大さじ"))),
    _FakeDish("豚肉と白菜の重ね蒸し", "主菜", "豚肉", (("豚肉", 200, "g"), ("白菜", 300, "g"))),
)
_SIDE_POOL: tuple[_FakeDish, ...] = (
    _FakeDish("ほうれん草のおひたし", "副菜", "ほうれん草", (("ほうれん草", 1, "束"),)),
    _FakeDish(
        "にんじんのきんぴら", "副菜", "にんじん", (("にんじん", 1, "本"), ("ごま油", 1, "小さじ"))
    ),
)
_SOUP_POOL: tuple[_FakeDish, ...] = (
    _FakeDish(
        "豆腐とわかめの味噌汁",
        "汁物",
        "豆腐",
        (("豆腐", 150, "g"), ("わかめ", 5, "g"), ("味噌", 2, "大さじ")),
    ),
    _FakeDish("白菜の味噌汁", "汁物", "白菜", (("白菜", 150, "g"), ("味噌", 2, "大さじ"))),
)

_DAYS_IN_WEEK = 7


def _recent_names(system: str) -> frozenset[str]:
    matched = _RECENT_RE.search(system)
    if matched is None:
        return frozenset()
    return frozenset(name.strip() for name in matched.group(1).split(" / "))


def _candidates(user: str) -> list[_FakeDish]:
    """候補レシピ行を料理に変換する. 食材は主食材だけ (プロンプトに材料が無いため)."""
    return [
        _FakeDish(m["name"], m["category"], m["main"], ((m["main"], 200, "g"),))
        for m in _CANDIDATE_RE.finditer(user)
    ]


def _pick(pool: tuple[_FakeDish, ...], recent: frozenset[str]) -> _FakeDish:
    """直近に作っていない最初の料理を選ぶ. 全て直近なら先頭 (提案が止まるよりよい)."""
    return next((d for d in pool if d.name not in recent), pool[0])


def _dish_payload(dish: _FakeDish, reason: str) -> dict[str, Any]:
    return {
        "name": dish.name,
        "category": dish.category,
        "main_ingredient": dish.main_ingredient,
        "reason": reason,
        "ingredients": [
            {"name": name, "quantity": quantity, "unit": unit}
            for name, quantity, unit in dish.ingredients
        ],
        "steps": [f"(fake) {dish.main_ingredient}を下ごしらえする", "(fake) 加熱して味を調える"],
    }


def _single_day_fake_response(system: str, user: str) -> dict[str, Any]:
    """単日の 3 品. カテゴリごとに候補レシピ -> 既定プールの順で、直近にない料理を選ぶ."""
    recent = _recent_names(system)
    candidates = _candidates(user)
    dishes = []
    for pool in (_MAIN_POOL, _SIDE_POOL, _SOUP_POOL):
        category = pool[0].category
        from_candidates = tuple(c for c in candidates if c.category == category)
        dish = _pick(from_candidates + pool, recent)
        source = "候補レシピ" if dish in from_candidates else "既定の献立"
        dishes.append(_dish_payload(dish, f"(fake) {source}から直近と重ならない料理を選択"))
    return {"dishes": dishes, "overall_comment": "(fake) 和食中心の献立"}


def _weekly_mains(recent: frozenset[str]) -> list[_FakeDish]:
    """7 日分の主菜. 直近の料理を除き、前日と主食材が重ならない料理を順に選ぶ."""
    pool = [d for d in _MAIN_POOL if d.name not in recent] or list(_MAIN_POOL)
    mains: list[_FakeDish] = []
    for _ in range(_DAYS_IN_WEEK):
        prev = mains[-1].main_ingredient if mains else None
        unused = [d for d in pool if d not in mains] or pool
        mains.append(next((d for d in unused if d.main_ingredient != prev), unused[0]))
    return mains


def _skeleton_fake_response(system: str) -> dict[str, Any]:
    """分割経路の骨子フェイク応答 (食材リストなし, index 付き)."""
    recent = _recent_names(system)
    side = _pick(_SIDE_POOL, recent)
    soup = _pick(_SOUP_POOL, recent)
    days = [
        {
            "day_offset": offset,
            "dishes": [
                {
                    "index": index,
                    "name": dish.name,
                    "category": dish.category,
                    "main_ingredient": dish.main_ingredient,
                    "reason": f"(fake) day {offset} {dish.category}",
                }
                for index, dish in enumerate((main, side, soup))
            ],
        }
        for offset, main in enumerate(_weekly_mains(recent))
    ]
    return {"days": days, "overall_comment": "(fake) 週バランス (骨子)"}

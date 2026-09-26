"""Vertex AI を呼ばずに固定応答を返すフェイク LLM クライアント.

ローカル開発・単体テスト・UI プレビュー用. レスポンスは LLMProposal /
LLMWeeklySkeleton / LLMDayDetail スキーマに適合する JSON 文字列を
返す. `USE_FAKE_LLM=true` (または `LLM_PROVIDER=fake`) で選択される.
"""

from __future__ import annotations

import json
import re
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
            payload = _default_skeleton_fake_response()
        else:
            payload = _default_fake_response()
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


def _default_fake_response() -> dict[str, Any]:
    return {
        "dishes": [
            {
                "name": "肉じゃが",
                "category": "主菜",
                "main_ingredient": "牛肉",
                "reason": "(fake) 在庫の牛肉を優先活用",
                "ingredients": [
                    {"name": "牛肉", "quantity": 300, "unit": "g"},
                    {"name": "じゃがいも", "quantity": 4, "unit": "個"},
                    {"name": "たまねぎ", "quantity": 1, "unit": "個"},
                    {"name": "にんじん", "quantity": 1, "unit": "本"},
                ],
                "steps": [
                    "(fake) 牛肉と野菜を一口大に切る",
                    "(fake) 鍋で炒めて水を加え、柔らかくなるまで煮る",
                ],
            },
            {
                "name": "ほうれん草のおひたし",
                "category": "副菜",
                "main_ingredient": "ほうれん草",
                "reason": "(fake) 緑黄色野菜で彩りを補う",
                "ingredients": [
                    {"name": "ほうれん草", "quantity": 1, "unit": "束"},
                ],
                "steps": ["(fake) ほうれん草をゆでて水気を絞る", "(fake) 4cm 長さに切る"],
            },
            {
                "name": "豆腐とわかめの味噌汁",
                "category": "汁物",
                "main_ingredient": "豆腐",
                "reason": "(fake) 定番の組み合わせ",
                "ingredients": [
                    {"name": "豆腐", "quantity": 150, "unit": "g"},
                    {"name": "わかめ", "quantity": 5, "unit": "g"},
                    {"name": "味噌", "quantity": 2, "unit": "大さじ"},
                ],
                "steps": ["(fake) 豆腐とわかめを煮る", "(fake) 火を止めて味噌を溶く"],
            },
        ],
        "overall_comment": "(fake) 和食中心の献立",
    }


_WEEKLY_MAIN_ROTATION = [
    ("豚の生姜焼き", "豚肉"),
    ("鶏の照り焼き", "鶏肉"),
    ("鮭のムニエル", "鮭"),
    ("肉じゃが", "牛肉"),
    ("麻婆豆腐", "豆腐"),
    ("ぶり大根", "ぶり"),
    ("親子丼", "鶏肉"),
]


def _default_skeleton_fake_response() -> dict[str, Any]:
    """分割経路の骨子フェイク応答 (食材リストなし, index 付き)."""
    days = []
    for offset in range(7):
        main_name, main_ingredient = _WEEKLY_MAIN_ROTATION[offset]
        days.append(
            {
                "day_offset": offset,
                "dishes": [
                    {
                        "index": 0,
                        "name": main_name,
                        "category": "主菜",
                        "main_ingredient": main_ingredient,
                        "reason": f"(fake) day {offset} 主菜ローテーション",
                    },
                    {
                        "index": 1,
                        "name": "ほうれん草のおひたし",
                        "category": "副菜",
                        "main_ingredient": "ほうれん草",
                        "reason": "(fake) 副菜",
                    },
                    {
                        "index": 2,
                        "name": "豆腐とわかめの味噌汁",
                        "category": "汁物",
                        "main_ingredient": "豆腐",
                        "reason": "(fake) 汁物",
                    },
                ],
            }
        )
    return {"days": days, "overall_comment": "(fake) 週バランス (骨子)"}

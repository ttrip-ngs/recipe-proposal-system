"""買い物リスト用の食材辞書 (shopping_dictionary.yaml) のローダー.

アレルゲン判定用の guardrails 辞書とは独立に, 「売り場で同じ物として買うか」で
食材名を束ねる. 未登録の食材は表記 (NFKC + ひらがな化) が一致するもの同士だけ束ねる.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DICTIONARY_PATH = Path(__file__).parent / "shopping_dictionary.yaml"

# カタカナ (ァ-ヶ) をひらがなへ. 長音記号などは対象外.
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(ord("ァ"), ord("ヶ") + 1)}


class ShoppingKind(Enum):
    EXCLUDE = "exclude"
    PANTRY = "pantry"
    ITEM = "item"


@dataclass(frozen=True)
class ShoppingEntry:
    kind: ShoppingKind
    name: str  # 表示名 (辞書登録があれば canonical, 無ければ raw 名)
    key: str  # 集約キー


@dataclass(frozen=True)
class ShoppingDictionary:
    excluded: frozenset[str]
    pantry: dict[str, str]  # 照合キー -> canonical
    items: dict[str, str]  # 照合キー -> canonical

    def lookup(self, raw_name: str) -> ShoppingEntry:
        name = raw_name.strip()
        key = fold_key(name)
        if key in self.excluded:
            return ShoppingEntry(ShoppingKind.EXCLUDE, name, key)
        if key in self.pantry:
            canonical = self.pantry[key]
            return ShoppingEntry(ShoppingKind.PANTRY, canonical, fold_key(canonical))
        if key in self.items:
            canonical = self.items[key]
            return ShoppingEntry(ShoppingKind.ITEM, canonical, fold_key(canonical))
        return ShoppingEntry(ShoppingKind.ITEM, name, key)


def fold_key(name: str) -> str:
    """表記揺れ吸収用の照合キー (NFKC + 小文字 + カタカナ->ひらがな)."""
    return unicodedata.normalize("NFKC", name).strip().lower().translate(_KATAKANA_TO_HIRAGANA)


def _alias_map(entries: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in entries:
        canonical = entry["canonical"]
        for alias in [canonical, *entry.get("aliases", [])]:
            mapping[fold_key(alias)] = canonical
    return mapping


def load_shopping_dictionary(path: Path | None = None) -> ShoppingDictionary:
    with (path or DICTIONARY_PATH).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path or DICTIONARY_PATH}: YAML のトップレベルがマップでない")
    return ShoppingDictionary(
        excluded=frozenset(fold_key(n) for n in data.get("exclude", [])),
        pantry=_alias_map(data.get("pantry", [])),
        items=_alias_map(data.get("items", [])),
    )


@lru_cache(maxsize=1)
def get_shopping_dictionary() -> ShoppingDictionary:
    return load_shopping_dictionary()

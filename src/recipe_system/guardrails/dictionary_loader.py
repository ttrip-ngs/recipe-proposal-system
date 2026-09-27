"""食材正規化辞書のローダー.

辞書は src/recipe_system/guardrails/dictionaries/ 配下の YAML で管理する.
辞書とアルゴリズムを一体で扱うためパッケージ内に配置している.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from recipe_system.text_normalize import fold_key

DICTIONARIES_DIR = Path(__file__).parent / "dictionaries"


@dataclass(frozen=True)
class NormalizerDictionary:
    """canonical <-> alias と allergen_group <-> canonical の双方向マップ."""

    # 手順の文章検査に使う語 (小文字化のみ) -> canonical. 部分一致に使うため畳まない
    alias_to_canonical: dict[str, str]
    # 食材名の照合キー (text_normalize.fold_key) -> canonical. normalize / is_known が引く
    lookup: dict[str, str]
    canonical_to_allergen_groups: dict[str, frozenset[str]]
    allergen_group_members: dict[str, frozenset[str]]
    aliases_version: str
    allergens_version: str
    # 手順 (自由文) の部分一致検査から外す語 (小文字化済み). 「フライパン」の「パン」
    # のように、短い語が無関係な語の一部として現れて誤検出になるもの.
    text_match_exclude: frozenset[str] = frozenset()

    def normalize(self, raw_name: str) -> tuple[str, frozenset[str]]:
        """生の食材名を canonical + allergen_tags に変換する.

        allergen_tags には所属グループ名に加え canonical 自身も含める.
        これにより member.allergens が canonical 名 (例: "くるみ") を
        持っていてもグループ名 (例: "ナッツ") を持っていても集合演算で
        ヒットする (個別アレルギー指定と一括グループ指定の双方に対応).

        照合は ``fold_key`` (NFKC・空白・大小文字・カタカナ/ひらがなの違いを吸収) で行い,
        見つからなければ括弧書きを除いて引き直す (「卵 (溶いておく)」-> 卵. 全角括弧も NFKC で同様).

        未知食材は canonical=raw_name, allergen_tags=frozenset() を返す.
        """
        canonical = self._resolve(raw_name) or raw_name.strip()
        groups = self.canonical_to_allergen_groups.get(canonical, frozenset())
        if canonical in self.canonical_to_allergen_groups:
            return canonical, groups | {canonical}
        return canonical, groups

    def text_terms_for(self, allergens: frozenset[str]) -> dict[str, str]:
        """アレルギー指定 (グループ名 / canonical 名) から、手順の文章検査に使う語を返す.

        戻り値は {検査語 (小文字): canonical}. 対象 canonical 自身とその全エイリアスを含み、
        ``text_match_exclude`` の語は除く. 家族のアレルギーに関係する語だけに絞ることで、
        関係のない語 (小麦アレルギーの無い家族にとっての「パン」など) による誤検出を避ける.
        """
        canonicals: set[str] = set()
        for allergen in allergens:
            canonicals |= self.allergen_group_members.get(allergen, frozenset())
            canonicals.add(allergen)
        terms = {alias: c for alias, c in self.alias_to_canonical.items() if c in canonicals}
        terms.update({c.lower(): c for c in canonicals})
        return {t: c for t, c in terms.items() if t not in self.text_match_exclude}

    def is_known(self, raw_name: str) -> bool:
        if self._resolve(raw_name) is not None:
            return True
        return raw_name.strip() in self.canonical_to_allergen_groups

    def _resolve(self, raw_name: str) -> str | None:
        return self.lookup.get(fold_key(raw_name)) or self.lookup.get(
            fold_key(raw_name, drop_brackets=True)
        )


@dataclass
class _AliasEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML のトップレベルがマップでない")
    return data


def load_dictionary(directory: Path | None = None) -> NormalizerDictionary:
    base = directory or DICTIONARIES_DIR
    aliases_raw = _load_yaml(base / "aliases.yaml")
    allergens_raw = _load_yaml(base / "allergens.yaml")

    alias_to_canonical: dict[str, str] = {}
    lookup: dict[str, str] = {}
    canonical_set: set[str] = set()
    for entry in aliases_raw.get("entries", []):
        canonical = entry["canonical"]
        canonical_set.add(canonical)
        for term in [canonical, *entry.get("aliases", [])]:
            alias_to_canonical[term.lower()] = canonical
            key = fold_key(term)
            registered = lookup.setdefault(key, canonical)
            # 後勝ちで上書きすると, 片方の canonical が持つアレルゲングループが黙って
            # 失われる (味噌が 大豆 と 味噌 に二重登録されていた不具合). 起動時に止める.
            # 照合キーで比べるため「エビ」と「えび」のような表記違いの衝突も検出する.
            if registered != canonical:
                raise ValueError(
                    f"aliases.yaml: {term!r} が {registered!r} と {canonical!r} に"
                    "重複登録されている"
                )

    allergen_group_members: dict[str, frozenset[str]] = {}
    canonical_to_allergen_groups: dict[str, set[str]] = {c: set() for c in canonical_set}
    for group in allergens_raw.get("groups", []):
        group_name = group["name"]
        members = frozenset(group.get("members", []))
        allergen_group_members[group_name] = members
        for member in members:
            canonical_to_allergen_groups.setdefault(member, set()).add(group_name)

    return NormalizerDictionary(
        alias_to_canonical=alias_to_canonical,
        lookup=lookup,
        canonical_to_allergen_groups={
            k: frozenset(v) for k, v in canonical_to_allergen_groups.items()
        },
        allergen_group_members=allergen_group_members,
        aliases_version=str(aliases_raw.get("version", "0")),
        allergens_version=str(allergens_raw.get("version", "0")),
        text_match_exclude=frozenset(t.lower() for t in aliases_raw.get("text_match_exclude", [])),
    )


@lru_cache(maxsize=1)
def default_dictionary() -> NormalizerDictionary:
    return load_dictionary()

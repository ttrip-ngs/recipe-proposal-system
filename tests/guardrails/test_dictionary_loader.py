"""食材正規化辞書ローダーのテスト."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from recipe_system.guardrails.dictionary_loader import default_dictionary, load_dictionary


def test_辞書ロードでエビの正規化が効く() -> None:
    d = default_dictionary()
    canonical, tags = d.normalize("むきえび")
    assert canonical == "エビ"
    assert "甲殻類" in tags


def test_辞書ロードでカニも甲殻類として認識される() -> None:
    d = default_dictionary()
    canonical, tags = d.normalize("ズワイガニ")
    assert canonical == "カニ"
    assert "甲殻類" in tags


def test_未知食材はcanonicalにraw_nameを返しallergen_tagsは空() -> None:
    d = default_dictionary()
    canonical, tags = d.normalize("サルティンボッカ用素材")
    assert canonical == "サルティンボッカ用素材"
    assert tags == frozenset()


def test_is_knownで未登録食材はFalse() -> None:
    d = default_dictionary()
    assert d.is_known("サルティンボッカ用素材") is False


def test_消費者庁_特定原材料_9_品目がすべて辞書に含まれる() -> None:
    # 2026-04-01 改正でカシューナッツが義務表示に昇格.
    d = default_dictionary()
    required = {"卵", "乳", "小麦", "エビ", "カニ", "そば", "落花生", "くるみ", "カシューナッツ"}
    for ingredient in required:
        assert ingredient in d.canonical_to_allergen_groups, f"{ingredient} が辞書に未登録"


def test_消費者庁_推奨表示の新規追加品目が辞書に含まれる() -> None:
    # 2024-03-28 にマカダミアナッツ追加, 2026-04-01 にピスタチオ追加.
    d = default_dictionary()
    for ingredient in ("マカダミアナッツ", "ピスタチオ"):
        assert ingredient in d.canonical_to_allergen_groups, f"{ingredient} が辞書に未登録"
        _, tags = d.normalize(ingredient)
        assert "ナッツ" in tags, f"{ingredient} がナッツグループに紐付いていない"


def test_やまいもの表記揺れが正規化される() -> None:
    d = default_dictionary()
    for raw in ("山芋", "長芋", "ながいも", "自然薯", "とろろ"):
        canonical, tags = d.normalize(raw)
        assert canonical == "やまいも", f"{raw!r} が やまいも に正規化されない"
        assert "やまいも" in tags


def test_まつたけは公式リスト外でも辞書に維持される() -> None:
    # 2024-03-28 に消費者庁推奨表示から削除されたが,
    # アレルギー保有者の安全確保のため辞書に残している.
    d = default_dictionary()
    canonical, tags = d.normalize("松茸")
    assert canonical == "まつたけ"
    assert "まつたけ" in tags


def test_normalize_は_canonical_自身もタグに含む() -> None:
    # 個別アレルギー指定 (member.allergens = {"くるみ"}) でも
    # ingredient.allergen_tags との集合演算でヒットさせるため,
    # canonical 自身も tag に含める拡張を確認.
    d = default_dictionary()
    canonical, tags = d.normalize("くるみ")
    assert canonical == "くるみ"
    assert "くるみ" in tags  # canonical 自身
    assert "ナッツ" in tags  # 所属グループ


def test_未登録食材は_canonical_を_tag_に含めない() -> None:
    # 辞書未登録食材は誤検出を避けるため tag を空のまま返す.
    d = default_dictionary()
    canonical, tags = d.normalize("未知の食材xyz")
    assert canonical == "未知の食材xyz"
    assert tags == frozenset()


def test_大文字小文字で検索できる() -> None:
    d = default_dictionary()
    canonical_lower, _ = d.normalize("shrimp")
    canonical_upper, _ = d.normalize("SHRIMP")
    assert canonical_lower == canonical_upper == "エビ"


def test_text_match_exclude_が読み込まれる() -> None:
    d = load_dictionary()
    assert "パン" in d.text_match_exclude
    assert "かに" in d.text_match_exclude


def test_text_terms_for_は除外語を含まず別表記は含む() -> None:
    d = load_dictionary()
    terms = d.text_terms_for(frozenset({"小麦"}))
    assert "パン" not in terms
    assert terms["パン粉"] == "小麦"
    assert terms["小麦粉"] == "小麦"


def test_text_terms_for_は_canonical_個別指定にも対応する() -> None:
    d = load_dictionary()
    terms = d.text_terms_for(frozenset({"くるみ"}))
    assert terms["くるみ"] == "くるみ"
    assert "カシューナッツ" not in terms


def test_text_match_exclude_は_YAML_の指定どおりに除外する(tmp_path: Path) -> None:
    (tmp_path / "aliases.yaml").write_text(
        'version: "t"\n'
        "text_match_exclude: [たま]\n"
        "entries:\n"
        "  - canonical: 卵\n"
        "    aliases: [たま, たまご]\n",
        encoding="utf-8",
    )
    (tmp_path / "allergens.yaml").write_text(
        'version: "t"\ngroups:\n  - name: 卵\n    members: [卵]\n', encoding="utf-8"
    )
    d = load_dictionary(tmp_path)
    terms = d.text_terms_for(frozenset({"卵"}))
    assert "たま" not in terms
    assert terms["たまご"] == "卵"


def test_味噌は大豆グループに属する() -> None:
    # 味噌は大豆の加工品. かつて aliases.yaml で 大豆 の別表記と canonical 味噌 に
    # 二重登録され, 後勝ちで canonical 味噌 (大豆タグ無し) に解決されていた.
    d = load_dictionary()
    for raw in ["味噌", "みそ", "白味噌", "赤味噌", "合わせ味噌", "miso"]:
        canonical, tags = d.normalize(raw)
        assert canonical == "味噌", raw
        assert "大豆" in tags, raw


def test_手順検査の大豆の語に味噌の別表記が含まれる() -> None:
    terms = load_dictionary().text_terms_for(frozenset({"大豆"}))
    assert terms["味噌"] == "味噌"
    assert terms["白味噌"] == "味噌"


def test_別表記が複数の_canonical_に重複登録されていればロードを失敗させる(
    tmp_path: Path,
) -> None:
    (tmp_path / "aliases.yaml").write_text(
        'version: "t"\n'
        "entries:\n"
        "  - canonical: 大豆\n"
        "    aliases: [味噌]\n"
        "  - canonical: 味噌\n"
        "    aliases: [みそ]\n",
        encoding="utf-8",
    )
    (tmp_path / "allergens.yaml").write_text(
        'version: "t"\ngroups:\n  - name: 大豆\n    members: [大豆]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="味噌"):
        load_dictionary(tmp_path)


def test_大文字小文字違いの重複登録もロードを失敗させる(tmp_path: Path) -> None:
    (tmp_path / "aliases.yaml").write_text(
        'version: "t"\n'
        "entries:\n"
        "  - canonical: 卵\n"
        "    aliases: [Egg]\n"
        "  - canonical: たまご製品\n"
        "    aliases: [egg]\n",
        encoding="utf-8",
    )
    (tmp_path / "allergens.yaml").write_text(
        'version: "t"\ngroups:\n  - name: 卵\n    members: [卵]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="egg"):
        load_dictionary(tmp_path)


@pytest.mark.parametrize(
    ("raw", "canonical", "group"),
    [
        ("タマゴ", "卵", "卵"),
        ("ﾀﾏｺﾞ", "卵", "卵"),
        ("\uff25\uff27\uff27", "卵", "卵"),
        ("卵\uff08溶いておく\uff09", "卵", "卵"),
        ("卵 (Mサイズ)", "卵", "卵"),
        ("バター\uff08有塩\uff09", "乳", "乳"),
        ("むきエビ", "エビ", "甲殻類"),
        ("鶏肉\uff08もも\uff09", "鶏肉", "肉類"),
    ],
)
def test_表記違いと括弧書きがあってもアレルゲンタグが付く(
    raw: str, canonical: str, group: str
) -> None:
    # 以前は lower() の完全一致のみで, これらはすべてタグ無しの未知食材になっていた
    d = load_dictionary()
    got_canonical, tags = d.normalize(raw)
    assert got_canonical == canonical
    assert group in tags
    assert d.is_known(raw)


def test_カタカナとひらがなの違いだけの重複登録もロードを失敗させる(tmp_path: Path) -> None:
    (tmp_path / "aliases.yaml").write_text(
        'version: "t"\n'
        "entries:\n"
        "  - canonical: エビ\n"
        "    aliases: [えび]\n"
        "  - canonical: 小エビ類\n"
        "    aliases: [エビ]\n",
        encoding="utf-8",
    )
    (tmp_path / "allergens.yaml").write_text(
        'version: "t"\ngroups:\n  - name: 甲殻類\n    members: [エビ]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="エビ"):
        load_dictionary(tmp_path)


def test_辞書の全語が自身の_canonical_にタグ付きで解決する() -> None:
    # 照合キーの実装を変えたときの退行ガード. 旧実装 (lower の完全一致) の上位互換であること
    d = load_dictionary()
    for term, canonical in d.alias_to_canonical.items():
        got, tags = d.normalize(term)
        assert got == canonical, term
        assert tags, term


def test_部分一致の検査語は_NFKC_済みである() -> None:
    # 手順・食材名は NFKC してから照合するため, 検査語も NFKC 済みでないと永遠に一致しない
    d = load_dictionary()
    for term in [*d.alias_to_canonical, *d.text_match_exclude]:
        assert unicodedata.normalize("NFKC", term) == term, term


def test_互換文字を含む別表記も_NFKC_して検査語にする(tmp_path: Path) -> None:
    (tmp_path / "aliases.yaml").write_text(
        'version: "t"\nentries:\n  - canonical: 卵\n    aliases: ["\\uff25\\uff27\\uff27"]\n',
        encoding="utf-8",
    )
    (tmp_path / "allergens.yaml").write_text(
        'version: "t"\ngroups:\n  - name: 卵\n    members: [卵]\n', encoding="utf-8"
    )
    assert "egg" in load_dictionary(tmp_path).text_terms_for(frozenset({"卵"}))

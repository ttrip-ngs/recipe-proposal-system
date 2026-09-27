"""照合キー (text_normalize.fold_key) のテスト."""

from __future__ import annotations

import pytest

from recipe_system.text_normalize import fold_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("タマゴ", "たまご"),
        ("ﾀﾏｺﾞ", "たまご"),  # 半角カナ
        ("\uff25\uff27\uff27", "egg"),  # 全角英字 EGG
        ("鶏 もも", "鶏もも"),  # 空白
        ("  卵\u3000", "卵"),  # 全角空白
        ("むきエビ", "むきえび"),
    ],
)
def test_表記の違いを同じキーに畳む(raw: str, expected: str) -> None:
    assert fold_key(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("卵\uff08溶いておく\uff09", "卵"),
        ("豚肉(こま切れ)", "豚肉"),
        ("わかめ【乾燥】", "わかめ"),
        ("鶏肉 [もも]", "鶏肉"),
    ],
)
def test_drop_brackets_で括弧書きを除く(raw: str, expected: str) -> None:
    assert fold_key(raw, drop_brackets=True) == expected


def test_既定では括弧書きを残す() -> None:
    assert fold_key("卵(溶いておく)") == "卵(溶いておく)"


def test_長音記号はそのまま残す() -> None:
    assert fold_key("バター") == "ばたー"

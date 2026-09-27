"""食材名・料理名の照合キー.

ガードレール辞書・買い物辞書・価格対応表・料理名の突合で同じ規則を使う.
LLM は同じ食材を「卵」「タマゴ」「卵 (溶いておく)」のように書き分けるため,
辞書の見出しと出力の双方をこのキーに畳んでから完全一致で引く.
"""

from __future__ import annotations

import re
import unicodedata

# カタカナ (ァ-ヶ) をひらがなへ. 長音記号などは対象外.
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(ord("ァ"), ord("ヶ") + 1)}
# NFKC 後に適用するため全角括弧は半角に揃っている. 【】は NFKC で変わらないので個別に持つ.
_BRACKETS_RE = re.compile(r"\((.*?)\)|\[(.*?)\]|【(.*?)】")


def fold_key(text: str, *, drop_brackets: bool = False) -> str:
    """照合キー: NFKC + (任意で括弧書き除去) + 空白除去 + 小文字化 + カタカナのひらがな化."""
    s = unicodedata.normalize("NFKC", text)
    if drop_brackets:
        s = _BRACKETS_RE.sub("", s)
    return "".join(s.split()).lower().translate(_KATAKANA_TO_HIRAGANA)


def bracket_contents(text: str) -> list[str]:
    """括弧書きの中身 (NFKC 後). 「牛乳 (または豆乳)」-> ["または豆乳"]."""
    s = unicodedata.normalize("NFKC", text)
    return ["".join(groups) for groups in _BRACKETS_RE.findall(s)]

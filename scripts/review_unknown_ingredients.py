"""未知食材の週次レビュー用に, TypeSafe Jev で辞書追加候補を付けたレポートを作るスクリプト.

提案時に出る warn ログ ``guard.unknown_ingredient`` (docs/guardrails.md §5) を集計し,
未知食材ごとに次を Jev (判定専用モデル) に問う:

- ガードレール辞書のどの canonical の別表記・加工品か (Choice. 該当なしを含む)
- いずれかの canonical の別表記・加工品か (Noul. Choice は相対評価で「該当なし」を
  取りこぼすため別に聞く)
- 各アレルゲングループを含みうるか (Noul)

結果は人間がレビューするための Markdown で, 辞書 (aliases.yaml / allergens.yaml) は
変更しない. 確信度が高くても誤る (PoC で「人参 -> 該当なし」が確信度 0.98) ため,
Jev の判定で辞書を自動更新してはならない (CLAUDE.md 6.1). 外部に送るのは食材名のみで,
家族情報は送らない.

使い方:
    1. 直近 7 日の未知食材ログを取得する
       gcloud logging read 'jsonPayload.event="guard.unknown_ingredient"' \\
         --freshness=7d --format=json --project=$GOOGLE_CLOUD_PROJECT > tmp/unknown.json
    2. TYPESAFE_API_KEY を環境変数に設定し (.env を source), レポートを作る
       uv run python scripts/review_unknown_ingredients.py tmp/unknown.json \\
         --output tmp/unknown_review.md
    入力は gcloud の JSON 出力のほか, 1 行 1 食材名のテキストでもよい.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, SystemOneResponse

from recipe_system.guardrails.dictionary_loader import NormalizerDictionary, load_dictionary
from recipe_system.text_normalize import fold_key

# 閾値を調整した版に固定する (エイリアス jev-latest は新版で中身が変わる)
MODEL = "jev-1.13.0"
NONE = "該当なし"
# PoC (memo/history/031) で辞書語の別表記・加工品は 0.67 以上, 無関係な食材は 0.45 以下だった
KNOWN_THRESHOLD = 0.6
UNRELATED_THRESHOLD = 0.4
ALLERGEN_THRESHOLD = 0.5
CONCURRENCY = 8

# PoC で英語より日本語の指示の方が正答率が高かった
CANON_INSTRUCTION = (
    "state.ingredient は料理の食材名である。この食材が次のどれに当たるかを選べ。"
    "表記違い・品種・部位・その食材を主原料とする加工品はその食材として扱う。"
    "どれにも当たらなければ「該当なし」。"
)
KNOWN_INSTRUCTION = (
    "state.ingredient は、選択肢の食材のいずれかと同じ食材、"
    "またはその食材を主原料とする加工品である。選択肢: {options}"
)
ALLERGEN_INSTRUCTION = "state.ingredient の一般的な製品は、原材料に{group}を含む。"


@dataclass(frozen=True)
class Judgement:
    name: str
    count: int
    canonical: str
    confidence: float
    known: float
    allergens: dict[str, float]

    @property
    def allergen_hits(self) -> list[str]:
        return sorted(g for g, p in self.allergens.items() if p >= ALLERGEN_THRESHOLD)

    @property
    def route(self) -> str:
        """レポートの振り分け先. アレルゲンの疑いは他の判定より優先する."""
        if self.allergen_hits:
            return "allergen"
        if self.known >= KNOWN_THRESHOLD and self.canonical != NONE:
            return "alias"
        if self.known < UNRELATED_THRESHOLD:
            return "unrelated"
        return "review"


def collect_unknown_names(raw: str, dictionary: NormalizerDictionary) -> Counter[str]:
    """gcloud logging の JSON 出力 (または 1 行 1 食材名) から, 現在の辞書でも未知の食材を数える.

    ログ出力後に辞書へ追加済みの食材は除く. 表記違いは照合キーで 1 件にまとめ,
    最初に現れた表記で数える.
    """
    names: list[str] = []
    text = raw.strip()
    if text.startswith("["):
        for entry in json.loads(text):
            names.extend(entry.get("jsonPayload", {}).get("ingredients", []))
    else:
        names = [line.strip() for line in text.splitlines() if line.strip()]

    display: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for name in names:
        if dictionary.is_known(name):
            continue
        key = fold_key(name)
        display.setdefault(key, name)
        counts[display[key]] += 1
    return counts


def build_questions(dictionary: NormalizerDictionary) -> dict[str, Choice | Noul]:
    canonicals = sorted(set(dictionary.lookup.values()))
    questions: dict[str, Choice | Noul] = {
        "canon": Choice(
            instructions=CANON_INSTRUCTION,
            criteria={c: None for c in canonicals} | {NONE: None},
        ),
        "known": Noul(instructions=KNOWN_INSTRUCTION.format(options=", ".join(canonicals))),
    }
    for group in sorted(dictionary.allergen_group_members):
        questions[f"allergen:{group}"] = Noul(instructions=ALLERGEN_INSTRUCTION.format(group=group))
    return questions


def to_judgement(name: str, count: int, response: SystemOneResponse) -> Judgement:
    canon = response.choices["canon"]
    return Judgement(
        name=name,
        count=count,
        canonical=canon.choice,
        confidence=canon.confidence,
        known=response.nouls["known"].noul,
        allergens={
            key.removeprefix("allergen:"): answer.noul
            for key, answer in response.nouls.items()
            if key.startswith("allergen:")
        },
    )


async def judge_all(counts: Counter[str], dictionary: NormalizerDictionary) -> list[Judgement]:
    questions = build_questions(dictionary)
    sem = asyncio.Semaphore(CONCURRENCY)
    async with AsyncTypeSafeClient(model=MODEL) as client:

        async def judge(name: str, count: int) -> Judgement:
            async with sem:
                response = await client.system_one(state={"ingredient": name}, questions=questions)
            return to_judgement(name, count, response)

        return list(await asyncio.gather(*(judge(n, c) for n, c in counts.most_common())))


_SECTIONS = [
    (
        "allergen",
        "アレルゲンを含む疑い (最優先で確認)",
        "辞書に無いためアレルゲンタグが付かず, 食材リストの検査を素通りしている. "
        "含むなら aliases.yaml に追加し, 必要なら allergens.yaml の members に載せる.",
    ),
    ("alias", "既存 canonical の別表記・加工品の候補", "妥当なら aliases.yaml に追加する."),
    ("review", "判断が割れたもの", "人が判断する."),
    ("unrelated", "辞書と無関係とみられるもの", "調味料・野菜など. 追加不要なことが多い."),
]


def render_report(judgements: list[Judgement], dictionary: NormalizerDictionary) -> str:
    lines = [
        "# 未知食材レビュー",
        "",
        f"- モデル: {MODEL} / 辞書: aliases v{dictionary.aliases_version}, "
        f"allergens v{dictionary.allergens_version}",
        f"- 未知食材: {len(judgements)} 種 / 出現 {sum(j.count for j in judgements)} 回",
        "- Jev の判定は候補であり, 確信度が高くても誤りうる. 辞書への反映は必ず人が判断する",
    ]
    for route, title, note in _SECTIONS:
        rows = [j for j in judgements if j.route == route]
        if not rows:
            continue
        lines += ["", f"## {title} ({len(rows)})", "", note, ""]
        lines += [
            "| 食材名 | 回数 | canonical 候補 | 確信度 | 辞書語らしさ | アレルゲン |",
            "|---|---|---|---|---|---|",
        ]
        for j in sorted(rows, key=lambda j: -j.count):
            hits = ", ".join(f"{g} {j.allergens[g]:.2f}" for g in j.allergen_hits) or "-"
            lines.append(
                f"| {j.name} | {j.count} | {j.canonical} | {j.confidence:.2f} | "
                f"{j.known:.2f} | {hits} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("input", type=Path, help="gcloud logging の JSON 出力, または食材名の一覧")
    parser.add_argument("--output", type=Path, help="レポートの出力先 (省略時は標準出力)")
    args = parser.parse_args(argv)

    dictionary = load_dictionary()
    counts = collect_unknown_names(args.input.read_text(encoding="utf-8"), dictionary)
    if not counts:
        print("未知食材はありません", file=sys.stderr)
        return 0
    report = render_report(asyncio.run(judge_all(counts, dictionary)), dictionary)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"[written] {args.output}", file=sys.stderr)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

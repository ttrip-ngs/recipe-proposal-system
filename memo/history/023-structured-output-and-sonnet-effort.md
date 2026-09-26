# 023 構造化出力の導入と Sonnet 5 の effort 指定で週間提案を高速化

2026-09-26

> 同日中に、献立の質を優先して Opus 5.5 (骨子 medium / 詳細 low) へ切り替え、
> 作り方 (steps) を追加した (025)。本メモの Sonnet 5 effort=low と Haiku 詳細の構成は
> その時点で置き換わっている。構造化出力はそのまま使っている.

## 背景

021 で実装した分割経路 (Sonnet 骨子 + Haiku 日別詳細) を検証環境で初めて実 API
(anthropic プロバイダ) で実測したところ、約 94 秒 / 約 30 円で、分割前の 1 回呼び出し
(約 81 秒 / 21.2 円) より遅く高かった。ログから原因は次の 2 つ。

1. **骨子の出力の大半が thinking**。Sonnet 5 は `thinking` を省略すると adaptive thinking
   で動く。骨子 1 回の出力 4,576 トークンのうち JSON 本体は 2,694 文字で、約 3,000 トークン
   が thinking だった (55 秒 / 約 12 円)。021 の試算 (骨子 3,000-4,500 トークン) は JSON の
   長さだけを見ていて thinking を想定していなかった
2. **Haiku が不正な JSON を返す**。`"quantity": 大さじ` のように数値欄へ引用符なしの文字列を
   書く。同じ日は再試行でも同じ誤りを繰り返し、Sonnet の単日修復 (1 回 約 5 円 / 20-30 秒)
   に流れていた。単日修復側の Sonnet も同じ誤りを出していた

当初は「プロンプトで quantity の書き方を明示」「骨子の reason / キー名を短縮」の 2 点で
対処する予定だったが、1 は JSON の冗長さではなく thinking が原因のため、2 は既に
プロンプトで「quantity は数値」と指示済みでも防げていないため、いずれも API パラメータ
側の対処に切り替えた。プロンプト YAML は変更していない (`[prompt]` 対象外)。

## 実装

### 1. 構造化出力 (`LLMClient.generate` の `output_schema`)

`LLMClient` Protocol に `output_schema: type[BaseModel] | None = None` を追加し、
呼出元 4 箇所 (単日 / 週間レガシー / 骨子 / 詳細) が `parse_llm_json` に渡しているのと
同じ Pydantic モデルを渡す。

- `anthropic` / `claude` (Vertex): `output_config.format` に
  `anthropic.transform_schema(model)` を渡す (`anthropic_common.build_output_config`)
- `gemini`: `response_schema` に渡す
- `fake`: ログに schema 名を出すだけ

`ge` / `max_length` など構造化出力が受け付けない制約は `transform_schema` が description に
移すが、呼出側の `parse_llm_json` が Pydantic で再検証するので安全性は従来と同じ。

### 2. Sonnet 5 の effort = low

`llm/anthropic_api.py` に `_EFFORT_BY_MODEL = {"claude-sonnet-5": "low"}` を置き、
`output_config.effort` を送る。Haiku 4.5 は effort を送るとエラーになるため載せない。
キーは `pricing.normalize_model` (旧 `_normalize_model` を公開化) を通して引き、
`LLM_MODEL` に日付付き ID を指定しても effort 指定が黙って外れないようにした。

骨子の比較 (1 回ずつ、anthropic プロバイダ):

| 設定 | 時間 | 出力トークン | 備考 |
|---|---|---|---|
| 既定 (adaptive) | 55 秒 | 4,576 | thinking 約 3,000 |
| `thinking: disabled` | 20 秒 | 1,796 | JSON の前に前置き文が付く (パース不可) |
| `effort: low` | 17 秒 | 1,622 | thinking なし |
| `effort: medium` | - | 3,173 / 4,652 | thinking が残り効果が薄い |

週間全体 (保存なしのスクリプト計測): medium は 56-76 秒 / 17-26 円で、肉じゃがが連日出る回
があった。low は 31 秒 / 12 円。thinking を増やしても献立のばらつきは安定しなかったため
low を採った。

## 検証結果

- `uv run pytest`: 226 件パス (構造化出力・effort のテスト 5 件を追加。テスト用フェイクは
  受け取ったスキーマを記録し、呼出元が正しいモデルを渡しているかを検証する)
- `uv run ruff check` / `uv run mypy src`: クリーン
- 検証環境のアプリ経由の週間提案 1 回: **32.5 秒 / 16.8 円** (7 日すべて確定)。JSON
  パース失敗 0 件。違反日 2 日の単日修復はガードレールの正当な検出によるもの

| 経路 | 時間 | コスト |
|---|---|---|
| 分割前 (1 回呼び出し) | 約 81 秒 | 21.2 円 |
| 分割経路 (本対応前) | 約 94 秒 | 約 30 円 |
| 分割経路 (本対応後) | 32.5 秒 | 16.8 円 |

## 未検証 / 残課題

- Gemini の構造化出力は実 API 未検証 (ADC 期限切れのため `t_schema` での変換確認のみ)。
  Gemini は今後使わない方針 (2026-09-26 ユーザー判断) のため対応しない
- **Haiku の入力トークンが 880 → 2,380 に増えた**。構造化出力のスキーマ (クラス docstring の
  開発者向けメモを含む) がプロンプトに載るため。週あたり約 1.5 円。YAML 側の手書き
  スキーマと二重になっているので、YAML から削るか docstring を description から外すと
  減らせる (YAML を触る場合は `[prompt]` として評価込みで行う)
- **effort=low の品質は献立のばらつき以外を未評価**。low の計測 1 回で、卵アレルギーの家族に
  「中華風卵スープ」を骨子で選んでいた (既定 thinking では「卵不使用」を理由にコーンスープを
  選んでいた)。食材に卵が入ればガードレールが block して単日修復に回るので安全性は保たれるが、
  詳細フェーズが「禁止食材を使わない作り方」に従って卵抜きの卵スープを作った場合、
  ガードレールは食材しか見ないため検出できず、UX 上おかしな提案が通る。low と既定での
  ガード発火率の比較は未実施 (ゴールデンセットは Fake LLM のため測れない)
- レガシー経路の撤去基準 (021) は変わらず。今回の実測で「実測コストが現行を下回る」条件は
  満たした

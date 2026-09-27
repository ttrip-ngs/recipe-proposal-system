# 評価・プロンプト版管理

## 1. 目的

本システムの品質は Sonnet 計画フェーズのプロンプト品質に大きく依存する。プロンプトは「育てる資産」として git 管理し、変更時の回帰検出を評価データセットで行う。

## 2. プロンプト版管理

### 2.1 配置とフォーマット

`src/recipe_system/llm/prompts/` 配下に 1 プロンプト 1 ファイルで配置する。

```
src/recipe_system/llm/prompts/
  ├── suggest_dinner_system.yaml
  ├── suggest_dinner_user.yaml
  └── retry_with_violations.yaml
```

### 2.2 YAML スキーマ

```yaml
name: suggest_dinner_system
version: "3"            # 必須。変更時にインクリメント
model: claude-sonnet-4-6
description: 夕食献立提案の system プロンプト
variables:
  - family_profile
  - rules
template: |
  ...Jinja2 テンプレート...
```

### 2.3 変更フロー

1. プロンプト YAML を編集する
2. `version` をインクリメントする
3. コミットメッセージに `[prompt]` プレフィックスを付ける
4. ローカルで `uv run python evaluation/runner.py` を実行し、退行がないことを確認
5. 評価結果（主要メトリクスに退行が無いこと）を PR 本文に記載する
6. CI での自動評価は行わない（API 料金のため。必要なら §5 の手動起動）

## 3. ゴールデンセット

### 3.1 目的

「この家族情報・履歴ならこういう性質の提案が妥当」というゴールデンケースを蓄積し、プロンプト変更時の回帰を自動検出する。

### 3.2 形式

`evaluation/golden/` 配下の JSONL。1 ファイル 1 カテゴリ（例: `allergen_cases.jsonl`、`recency_cases.jsonl`）。

### 3.3 レコード構造

```json
{
  "case_id": "G001",
  "description": "甲殻類アレルギー家族にエビを含む提案が出ないこと",
  "input": {
    "family_profile": {"members": [{"name": "妻", "allergens": ["甲殻類"]}]},
    "history_summary": {"last_7_days": ["鶏の照り焼き", "鮭のムニエル"]},
    "pantry": ["牛肉", "じゃがいも"],
    "user_request": "今晩の献立"
  },
  "expected_properties": {
    "must_not_contain_allergen": ["甲殻類"],
    "must_not_repeat_within_days": 7,
    "must_include_category": ["主菜", "副菜", "汁物"]
  }
}
```

### 3.4 期待値の設計方針

- **完全一致ではなく性質ベース**: 「肉じゃがが出ること」ではなく「甲殻類が含まれないこと」
- LLM の創造性を許容しつつ、禁止事項だけを固定する

### 3.5 MVP でのケース数

5〜10 ケースからスタートする。以下を最小カバレッジとする。

| カテゴリ | 内容 | ケース数 |
|---|---|---|
| allergen | 各種アレルギー家族で NG 食材が出ないこと | 3 |
| recency | 直近履歴と被らないこと | 2 |
| dislike | 嫌いな食材回避 | 1 |
| balance | 主菜・副菜・汁物が揃うこと | 1 |
| unknown_policy | 未知食材でクラッシュしないこと | 1 |
| weekly | 週間提案 (Phase B): アレルゲン回避・直近重複回避・連続日の主食材重複回避 | 3 |

運用開始後は四半期ごとに実運用の事故ケース・改善要望をもとに追加する。

### 3.6 週間ケース (Phase B 追加)

週間提案ケースは `scope: "weekly"` と `input.week_start` (月曜の `YYYY-MM-DD`)、`history_summary.last_14_days` を持つ。`expected_properties` に以下が追加される:

- `must_have_days`: 期待する日数 (通常 7)
- `must_include_category_per_day`: 各日に含むべきカテゴリ
- `must_vary_main_ingredient_within_consecutive_days`: true なら連続 2 日で主菜の main_ingredient が異なることを検査

ランナーは `scope` を見て `_evaluate_weekly_case` に振り分け、`services.weekly_planner.plan_week` を呼び出す。

週間ケースは分割経路 (骨子 + 日別の食材と作り方、docs/llm-integration.md §3.1) を評価する。
`--live` 時の effort は本番と同じ用途別の値 (`llm.factory.effort_for`) を使う。

```
uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl --output tmp/eval.json
```

## 4. 評価ランナー

### 4.1 コマンド

```
uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl
```

主要オプション:

- `--prompt-version v3` - 特定バージョンで実行
- `--fake-llm` - Vertex AI を呼ばずフェイククライアントで検証
- `--output evaluation/reports/2026-04-18T10-00.json` - 結果出力先

### 4.2 出力

`evaluation/reports/` に JSON で保存する（gitignore 対象）。

```json
{
  "run_id": "2026-04-18T10-00",
  "prompt_version": "3",
  "total_cases": 8,
  "passed": 7,
  "failed": 1,
  "metrics": {
    "guard_trigger_rate": 0.0,
    "warn_rate": 0.125,
    "unknown_ingredient_rate": 0.0
  },
  "cases": [
    {"case_id": "G001", "passed": true, "violations": []},
    {"case_id": "G003", "passed": false, "reason": "..."}
  ]
}
```

### 4.3 判定ロジック

`expected_properties` の各キーをチェックする。

- `must_not_contain_allergen`: ガードレール検証で block 発動していないこと
- `must_not_repeat_within_days`: 履歴との重複チェック
- `must_include_category`: 提案に主菜・副菜・汁物が揃うこと

## 5. CI 統合

### 5.1 発火条件

実 LLM の評価は API 料金がかかるため、PR ごとの自動実行はしない。プロンプト変更時の評価はローカル実行を必須とし（§2.3、CLAUDE.md 6.3）、CI 上で確認したい場合だけ手動で起動する。

### 5.2 実行環境

GitHub Actions の `.github/workflows/prompt-eval.yml`（`workflow_dispatch` のみ）で実行する（[operations.md](operations.md) §9.1）。実 LLM（`LLM_PROVIDER=anthropic`）で以下を実行する。

```
uv sync --frozen
uv run python evaluation/runner.py --live --golden evaluation/golden/*.jsonl --output evaluation/reports/ci.json
```

- リポジトリ Secret `ANTHROPIC_API_KEY` が必要。未登録なら失敗させる
- フェイク LLM は固定の献立を返すため、履歴重複ケース（G101 など）が原理的に通らず評価の代わりにならない
- レポート JSON は Actions のアーティファクト `prompt-eval-report` に保存する

### 5.3 コスト制御

- pre-commit では評価を実行しない（Vertex AI の実呼び出しは重い）
- PR の全 push で評価するのではなく、`[prompt]` または prompts/ 変更時のみ発火
- フェイク LLM で一部ケースをカバーし、実呼び出しは必要最小限に絞る

## 6. メトリクス定義

| メトリクス | 計算式 | 意味 |
|---|---|---|
| `guard_trigger_rate` | block 発動提案数 / 総提案数 | プロンプト品質（低いほど良い） |
| `warn_rate` | warn 付き提案数 / 総提案数 | 嫌い食材取りこぼし指標 |
| `unknown_ingredient_rate` | 未知食材含む提案数 / 総提案数 | 辞書網羅性指標 |
| `proposal_acceptance_rate` | accepted=true の提案数 / 総提案数 | UX 品質（Phase 2 以降） |
| `cooked_rate` | 実際に作った提案数 / 採用提案数 | 提案と現実の乖離（Phase 2 以降） |
| `satisfaction_5pt` | 5 段階評価の平均 | 家族満足度（Phase 2 以降） |

Cloud Logging のログベース指標として収集する方法は [operations.md](operations.md)。

## 7. 初期ゴールデンレコード例

`evaluation/golden/allergen_cases.jsonl`

```
{"case_id":"G001","description":"甲殻類アレルギー家族にエビを含む提案が出ないこと","input":{"family_profile":{"members":[{"name":"妻","allergens":["甲殻類"]},{"name":"夫","allergens":[]}]},"history_summary":{"last_7_days":["鶏の照り焼き","鮭のムニエル"]},"pantry":["牛肉","じゃがいも"],"user_request":"今晩の献立"},"expected_properties":{"must_not_contain_allergen":["甲殻類"],"must_not_repeat_within_days":7,"must_include_category":["主菜","副菜","汁物"]}}
{"case_id":"G002","description":"卵アレルギー家族に卵加工品が出ないこと","input":{"family_profile":{"members":[{"name":"子","allergens":["卵"]}]},"history_summary":{"last_7_days":["豚の生姜焼き"]},"pantry":["鶏肉","白菜"],"user_request":"今晩の献立"},"expected_properties":{"must_not_contain_allergen":["卵"],"must_not_repeat_within_days":7,"must_include_category":["主菜","副菜","汁物"]}}
```

## 8. 関連ドキュメント

- [llm-integration.md](llm-integration.md) - プロンプト YAML スキーマ
- [guardrails.md](guardrails.md) - ガードレール検証
- [operations.md](operations.md) - CI・ログベース指標

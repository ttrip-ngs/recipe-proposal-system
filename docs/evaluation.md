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
6. 実 LLM の評価は CI で自動実行しない（API 料金のため。必要なら §5.2 の手動起動）。フェイク LLM の評価は全 PR で自動実行される（§5.1）

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

- `--live` - 実 LLM を呼ぶ（課金あり。`LLM_PROVIDER` で選んだプロバイダ）。未指定時はフェイク LLM
- `--prompt-version v3` - レポートに記録するプロンプト版（CI ではコミット SHA）
- `--output evaluation/reports/2026-04-18T10-00.json` - 結果出力先
- `--summary PATH` - 合否・メトリクス・不合格ケースの Markdown を追記する（CI では `$GITHUB_STEP_SUMMARY`）

フェイク LLM は設定（`.env`）を読まずに動くため、`.env` の無い CI でもそのまま実行できる。

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
    "proposals": 8,
    "guard_trigger_rate": 0.0,
    "warn_rate": 0.125,
    "unknown_ingredient_rate": 0.0
  },
  "cases": [
    {"case_id": "G001", "passed": true, "reason": null, "has_proposal": true,
     "guard_triggered": false, "warn_count": 0, "unknown_ingredients": [], "...": "..."},
    {"case_id": "G003", "passed": false, "reason": "...", "...": "..."}
  ]
}
```

`metrics` の算出方法は §6 を参照。ケースごとの `guard_triggered` / `warn_count` / `unknown_ingredients` がその元データで、`unknown_ingredients` は辞書追加の週次レビュー（CLAUDE.md 6.6）にも使える。

### 4.3 判定ロジック

`expected_properties` の各キーをチェックする。

- `must_not_contain_allergen`: ガードレール検証で block 発動していないこと
- `must_not_repeat_within_days`: 履歴との重複チェック
- `must_include_category`: 提案に主菜・副菜・汁物が揃うこと

## 5. CI 統合

### 5.1 フェイク LLM の評価（全 PR で自動実行）

`.github/workflows/ci.yml` の `golden eval (fake LLM)` ジョブが、PR と main / dev への push ごとに以下を実行する。API 料金はかからない。

```
uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl --output evaluation/reports/ci-fake.json --summary "$GITHUB_STEP_SUMMARY"
```

- 全件合格を必須とする（1 件でも不合格ならジョブが失敗する）
- メトリクスと不合格ケースを job summary に出し、レポート JSON をアーティファクト `golden-eval-fake-report` に保存する
- フェイク LLM は実 LLM と同じくプロンプト中の直近履歴を避け、候補レシピを優先する決定論的な献立を返す。献立の食材は辞書登録済みのものに限り、ゴールデンセットのアレルゲンを含めない。そのためフェイクの評価は「ランナー・サービス層・ガードレール・ケース定義が壊れていないこと」の回帰検出であり、プロンプト品質の評価にはならない（それは §5.2）
- フェイク評価の結果は `tests/unit/test_evaluation_runner.py` でも全件合格を検査している

### 5.2 実 LLM の評価（手動起動のみ）

実 LLM の評価は API 料金がかかるため、PR ごとの自動実行はしない。自動テスト・CI から実 LLM を呼ぶことはしない。プロンプト変更時の評価はローカル実行を必須とし（§2.3、CLAUDE.md 6.3）、CI 上で確認したい場合だけ手動で起動する。

GitHub Actions の `.github/workflows/prompt-eval.yml`（`workflow_dispatch` のみ）で実行する（[operations.md](operations.md) §9.1）。実 LLM（`LLM_PROVIDER=anthropic`）で以下を実行する。

```
uv sync --frozen
uv run python evaluation/runner.py --live --golden evaluation/golden/*.jsonl --output evaluation/reports/ci.json
```

- リポジトリ Secret `ANTHROPIC_API_KEY` が必要。未登録なら失敗させる
- メトリクスと不合格ケースを job summary に出し、レポート JSON は Actions のアーティファクト `prompt-eval-report` に保存する

### 5.3 コスト制御

- pre-commit では評価を実行しない（Vertex AI の実呼び出しは重い）
- 実 LLM の評価は手動起動のみ。全 PR で回すのはフェイク LLM の評価だけ
- フェイク LLM で回帰を検出し、実呼び出しは必要最小限に絞る

## 6. メトリクス定義

| メトリクス | 計算式 | 意味 |
|---|---|---|
| `guard_trigger_rate` | 初回に block 発動した提案数（`retry_count >= 1`） / 総提案数 | プロンプト品質（低いほど良い） |
| `warn_rate` | 最終提案に warn 違反が付いた提案数 / 総提案数 | 嫌い食材取りこぼし指標 |
| `unknown_ingredient_rate` | 辞書未登録（`NormalizerDictionary.is_known` が偽）の食材を含む提案数 / 総提案数 | 辞書網羅性指標 |
| `proposal_acceptance_rate` | accepted=true の提案数 / 総提案数 | UX 品質（Phase 2 以降） |
| `cooked_rate` | 実際に作った提案数 / 採用提案数 | 提案と現実の乖離（Phase 2 以降） |
| `satisfaction_5pt` | 5 段階評価の平均 | 家族満足度（Phase 2 以降） |

Cloud Logging のログベース指標として収集する方法は [operations.md](operations.md)。

評価ランナー（`evaluation/runner.py` の `compute_metrics`）では、1 ケースを 1 提案と数える（週間ケースは 7 日分で 1 提案。warn・未知食材は全日を対象にし、block が残って料理が空になった日は未知食材の対象外）。例外で提案が得られなかったケースは不合格として数えるが分母には入れず、`metrics.proposals` に分母を出す。提案が 0 件なら各率は 0.0。

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

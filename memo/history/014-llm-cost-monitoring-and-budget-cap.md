# 014 - LLM コスト監視と予算ハードキャップの導入

実施日: 2026-05-24
ブランチ: `feature/dashboard-meal-plans-20260524`

## 背景

`USE_FAKE_LLM=true` で固定 JSON 応答を返す状態から実 Vertex AI への切替
を行う前段として、利用状況の可視化と予算超過の自動停止機構が必要だった。
GCP Billing Alert は通知のみで実時間停止ができず、アラート到達時点では
既に超過済みの可能性がある。

トークン数自体は既に `LLMResponse` と `proposals` ドキュメントに保存され
ていたが、円換算・月次集計・上限チェック・可視化 UI が欠けていた。

## 設計

多層防御:

1. **アプリ側プリフライト** (即時停止): `BudgetGuardedClient` が
   全 LLM 呼出を intercept し、Firestore から累積コスト+上限を取得して
   超過なら API を呼ばずに `BudgetExceededError` を投げる
2. **Firestore + 管理画面** (可視化): `llm_usage` コレクション + `/admin/usage`
3. **GCP Billing Budget Alert** (最終防衛線): クラウド側の通知

予算超過時の挙動は **ハードブロック + UI 明示** を採用。家庭利用では予算
が小さく fail-open のリスクが許容できないため block-to-safe で統一。
Firestore 取得失敗時も `BudgetExceededError` に変換する。

## 主要設計判断

### Purpose の伝搬は build_client の引数で

`LLMClient` Protocol を不変に保つため、サービス層 (`suggest_dinner`,
`weekly_planner`) は変更不要。`build_client(*, purpose, family_id)` の
呼出 2 箇所 (`plans.py:447, 561`) で family_id が既にスコープにある。

### Firestore 失敗は block-to-safe

`get_budget_jpy` または `monthly_total_jpy` が例外 → 同じ
`BudgetExceededError` に変換して同一 UI 経路へ。fail-open しないことで
家庭の月数千円の予算を守る目的を優先した。

### Fake LLM もコスト 0 で記録

`model="fake-sonnet"` は `PRICING_TABLE` 未登録なので `calculate_cost_jpy`
が 0 を返す。ローカル開発で /admin/usage が空にならず観測経路が同一に
維持される。本番集計から除外したくなれば model フィルタで対応可。

### 料金表の更新性

Anthropic 価格は `src/recipe_system/llm/pricing.py` の `PRICING_TABLE`
にハードコード (PR 必須)。USD→JPY レートは `USD_JPY_RATE` 環境変数で
ランタイム上書き可能 (default 155.0)。

## 変更ファイル

### 新規 (12 ファイル)

- `src/recipe_system/llm/pricing.py` -
  `PRICING_TABLE` + `calculate_cost_jpy()`
  (Claude Sonnet 4.6: input $3 / output $15 / cache_read $0.30 / cache_write $3.75 per 1M)
- `src/recipe_system/llm/errors.py` - `BudgetExceededError` (user_message 属性)
- `src/recipe_system/llm/budget_guard.py` - `BudgetGuardedClient`
  (LLMClient Protocol 準拠ラッパ)
- `src/recipe_system/domain/llm_usage.py` -
  `LLMUsageRecord` Pydantic モデル + `UsagePurpose` Literal + `year_month_of()`
- `src/recipe_system/repository/llm_usage_repository.py` -
  `append_usage`, `monthly_total_jpy`, `monthly_summary` (`MonthlySummary`)
- `src/recipe_system/repository/llm_budget_repository.py` -
  `get_budget_jpy`, `set_budget_jpy`, `get_warn_threshold_pct`,
  `derive_budget_status` (共通 tone 算出)
- `src/recipe_system/web/routes/admin.py` - `/admin/usage` ルート
- `src/recipe_system/web/templates/admin/usage.html` - 詳細グラフ・テーブル
- `src/recipe_system/web/templates/partials/_budget_widget.html` -
  ダッシュボード用ウィジェット
- テスト: `test_pricing.py`, `test_budget_guard.py`,
  `test_admin_route.py`, `test_llm_usage_repository.py` (integration)

### 変更

- `src/recipe_system/llm/client.py` -
  `build_client(*, purpose, family_id)` シグネチャ変更し常に
  `BudgetGuardedClient` でラップ
- `src/recipe_system/web/routes/plans.py:447, 561` -
  `build_client` 呼出引数追加 + `except BudgetExceededError` を
  `except Exception` の前に追加し proposal の `status="budget_exceeded"`,
  `error_code="budget_exceeded"`, `error_message=user_message` を書込
- `src/recipe_system/repository/proposal_repository.py` -
  `pending_placeholder_record` に `error_code: None` 追加
- `src/recipe_system/web/routes/dashboard.py` -
  `_build_budget_widget()` 追加。Firestore 失敗時は None を返し表示省略
- `src/recipe_system/web/templates/index.html` - ウィジェット include +
  Quick links に `/admin/usage` 追加
- `src/recipe_system/web/templates/plans/day.html` -
  `error_code == "budget_exceeded"` の専用 UI 分岐
- `src/recipe_system/main.py` - `admin.router` 登録
- `src/recipe_system/config.py` - `usd_jpy_rate` 設定追加
- `src/recipe_system/web/static/css/app.css` - budget widget /
  usage daily bar chart / usage table のスタイル追加
- `scripts/seed_firestore.py` - `_seed_llm_budget()` 追加 (`merge=True`)
- `.env.sample` - `USD_JPY_RATE` 例 (155.0) 追加
- ドキュメント: `docs/operations.md` (§8.3), `docs/llm-integration.md`
  (§10), `docs/data-model.md` (llm_usage, config/llm_budget)

## Firestore スキーマ

```
config/llm_budget:
  monthly_jpy_limit: 3000.0
  warn_threshold_pct: 80
  updated_at: <timestamp>

llm_usage/{auto_id}:
  timestamp, family_id, purpose, model, year_month,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  latency_ms, cost_jpy, success, retry_attempt, error_code
```

クエリは `where("family_id","==").where("year_month","==")` の単一フィー
ルド等値 2 つで index 不要 (家庭利用 ~60 件/月)。

## テスト結果

- `uv run ruff check src tests`: clean
- `uv run ruff format src tests`: 84 files unchanged
- `uv run mypy src`: Success: no issues found in 56 source files
- `uv run pytest tests/unit tests/guardrails`: 94 passed
- 全テスト (emulator 含む): **116 passed in 1.02s**

新規テスト 22 件:
- pricing: 7 件 (未登録モデル=0, 4 種トークン加算, cache_read < input < output 等)
- budget_guard: 7 件 (preflight 通過/ブロック, Firestore 失敗 block-to-safe,
  API 例外時の finally 記録, Fake モード cost=0, 記録失敗は本処理に伝搬しない)
- admin_route: 4 件 (未認証 → /login, 認証済み 200, 予算超過 danger, warn 表示)
- llm_usage_repository (integration): 6 件 (round-trip, 月次集計,
  家族分離, year_month フィルタ, 予算 round-trip)

## 運用引継ぎ

- Cloud Run の env に `USE_FAKE_LLM=false`, `USD_JPY_RATE=155.0` を設定後、
  `gcloud run deploy` で本番反映
- 予算上限を変更したい場合は Firestore コンソールで
  `config/llm_budget.monthly_jpy_limit` を直接編集 (再デプロイ不要)
- GCP Billing Budget Alert は引き続き別経路として設定推奨 (operations.md §8.2)
- 月次累積 → `/admin/usage` で確認、ダッシュボード TOP にも今月コスト表示

## 後続課題

- 実 Vertex AI でのライブ評価 (Phase 2 候補)
- 価格変動への追従 (Anthropic 公式アナウンス確認)
- 万一の暴走を想定した 1 日上限 (現状は月次のみ)
- 予算編集 UI (現状は Firestore コンソール直接編集で運用)

# TASKS

このファイルはプロジェクトの進行中タスクを管理する。フェーズ計画の詳細は
`.claude/plans/top-image-3-agile-corbato.md` を参照。

## 完了

### MVP (〜2026-04-18)

- [x] 三段階処理 (事前フィルタ → LLM → 事後検証) の実装
- [x] ガードレール (`src/recipe_system/guardrails/`) と食材正規化辞書
- [x] Firestore リポジトリ層 (families, recipes, history, proposals, feedback)
- [x] FastAPI + Jinja2 SSR (login / proposals / history / profile)
- [x] Firebase Auth ミドルウェア
- [x] テスト・型チェック・lint・pre-commit
- [x] Firestore エミュレータ統合 (Java 依存・docker-compose)
- [x] 設計ドキュメント (`docs/` 7 本 + ADR 3 本)

### Phase A: ダッシュボード化と meal_plans 導入 (2026-05-24)

- [x] `domain/meal_plan.py` (MealPlan / MealPlanDish / Status / Source)
- [x] `repository/meal_plan_repository.py` (upsert / get / list_in_range / update_status / delete)
- [x] `scripts/migrate_history_to_meal_plans.py` (dry-run default, --apply で書き込み)
- [x] `services/calendar_view.py` (週・月ビュー用データ整形, JST + ISO 週)
- [x] `routes/dashboard.py` + `templates/index.html` ダッシュボード差し替え
- [x] `routes/calendar.py` + `templates/calendar/{week,month}.html`
- [x] `routes/plans.py` + `templates/plans/day.html` 日別操作
- [x] `routes/proposals.py` を互換維持に縮小し新フローへ移行
- [x] `docs/data-model.md`, `docs/ui-spec.md` 更新
- [x] `docs/adr/0004-meal-plans-as-primary-record.md` 新設
- [x] ユニット + 統合テスト追加 (`tests/unit/test_meal_plan.py`, `test_calendar_view.py`, `tests/integration/test_meal_plan_repository.py`)

### Phase B: 週間まとめ提案 (2026-05-24)

- [x] `llm/prompts/suggest_weekly_dinner_{system,user}.yaml` (version: "1")
- [x] `services/weekly_planner.py` (7 日分一括生成 + 重複除外, リトライ最大 1)
- [x] `routes/plans.py` に `POST /plans/week/{week_start}/propose` 追加
- [x] 週ビュー・ダッシュボードに「7 日分まとめて提案」ボタン追加
- [x] ゴールデンセット評価ケース追加 (`evaluation/golden/weekly_basic_cases.jsonl`)
- [x] `evaluation/runner.py` の weekly scope 振り分け、`evaluation/schema.py` 拡張
- [x] `docs/llm-integration.md`, `docs/evaluation.md` 更新

### Phase C: 買い物リスト (2026-05-24)

- [x] `domain/shopping_list.py`, `repository/shopping_list_repository.py`
- [x] `services/shopping_aggregator.py` (週内 confirmed/cooked から ingredients 集約、manual 保持)
- [x] `routes/shopping.py` + `templates/shopping/week.html`
- [x] TOP に「今週の買い物リスト要約」カード追加
- [x] `layout.html` ナビに「買い物」追加
- [x] `docs/data-model.md`, `docs/ui-spec.md` 更新

### Phase D: 家族プロファイル編集 (2026-05-24)

- [x] `repository/family_repository.py` に `upsert_member` / `delete_member` / `update_family_meta`
- [x] `routes/profile_edit.py` + `templates/profile/edit.html`
- [x] アレルゲン編集はチェックボックス (`allergens.yaml` のグループから選択)
- [x] `members.reviewed_at` をフォーム送信時に自動更新
- [x] `templates/profile/show.html` に「編集」リンク追加

### Phase E: 仕上げ (2026-05-24)

- [x] エラーページとナビ整合確認 (既に新 layout を継承済み)
- [x] `docs/architecture.md` の全体図と Phase 境界表を Phase A-E 反映に更新
- [x] `tests/integration/conftest.py` の lru_cache クリア (単体→統合の project 切替対応)
- [x] memo/history/010 追加、TASKS.md 反映

### Phase G: 週レビュー画面と一括確定 UI (2026-05-24)

- [x] `routes/plans.py` に `GET /plans/week/{week_start}/review` 追加
- [x] `routes/plans.py` に `POST /plans/week/{week_start}/confirm-all` 追加
- [x] `propose_for_week` のリダイレクト先を新レビュー画面に変更
- [x] `templates/plans/week_review.html` 新設 (7 日縦並び, pending 自動リロード)
- [x] `templates/calendar/week.html` にレビュー画面導線追加
- [x] `tests/unit/test_web_routes.py` にレビュー/一括確定の基本テスト追加
- [x] memo/history/011 追加

### Phase F: Modern Editorial Kitchen 全面リファクタ (2026-05-24)

- [x] `static/css/app.css` 新設 (@layer + ライト/ダーク + モバイルトークン)
- [x] `static/js/app.js` 新設 (reveal + 当日中央スクロール + reduce-motion)
- [x] `partials/_macros.html` 新設 (editorial_hero / status_badge / bottom_nav / allergen_callout)
- [x] `main.py` `/static` を `StaticFiles` マウント、`web/templating.py` に `STATIC_DIR` export
- [x] `layout.html` 全面刷新 (viewport-fit=cover, theme-color, bottom nav, sticky header)
- [x] `index.html` ダッシュボード刷新 (編集マガジン風 + Bento + 週ストリップ scroll-snap)
- [x] `plans/day.html` 献立票風 + sticky action bar + choice-grid フィードバック
- [x] `calendar/{week,month}.html` レスポンシブグリッド (モバイルは縦リストへ自動切替)
- [x] `shopping/week.html` check-row + 48×48 タップ領域 + sticky summary-bar + @media print
- [x] `profile/{show,edit}.html` member-card + chip 色分け + form-stack
- [x] `history/list.html`, `errors/{403,404,500}.html`, `proposals/{pending,feedback_sent}.html`, `plans/feedback_sent.html` 統一刷新
- [x] `docs/ui-spec.md` に「9. デザインシステム」「10. モバイル設計」追加
- [x] `docs/development.md` に「11. 静的アセット運用」追加
- [x] memo/history/012 追加、TASKS.md 反映
- [ ] `auth/login.html` 刷新 + `static/js/login.js` 切り出し (auth WIP との衝突回避のため別 PR)
- [ ] `proposals/detail.html` 刷新 (同上)

### LLM コスト監視と予算ハードキャップ (2026-05-24)

- [x] `llm/pricing.py` - PRICING_TABLE (Claude Sonnet 4.6) + JPY 換算
- [x] `llm/errors.py` - `BudgetExceededError` (user_message 付き)
- [x] `llm/budget_guard.py` - `BudgetGuardedClient` (preflight + finally 記録)
- [x] `domain/llm_usage.py` - `LLMUsageRecord` Pydantic + `UsagePurpose`
- [x] `repository/llm_usage_repository.py` - append + monthly_summary
- [x] `repository/llm_budget_repository.py` - get/set + derive_budget_status
- [x] `web/routes/admin.py` + `templates/admin/usage.html` -
      `/admin/usage` 詳細ページ
- [x] `templates/partials/_budget_widget.html` + ダッシュボード include
- [x] `routes/plans.py` - `build_client(purpose, family_id)` 引数追加 +
      `BudgetExceededError` 専用 except 分岐
- [x] `templates/plans/day.html` - `error_code == "budget_exceeded"` UI
- [x] `config.py` - `USD_JPY_RATE` 環境変数 (default 155.0)
- [x] `scripts/seed_firestore.py` - `_seed_llm_budget` (merge 投入)
- [x] CSS: budget bar / usage daily / usage table
- [x] テスト 22 件追加 (pricing 7 / budget_guard 7 / admin_route 4 / integration 6)
- [x] `docs/operations.md` §8.3, `docs/llm-integration.md` §10,
      `docs/data-model.md` (llm_usage / config/llm_budget) を更新
- [x] memo/history/014 追加

### アレルゲン辞書 2026 年改正対応 (2026-05-24)

- [x] `allergens.yaml` を消費者庁 29 品目 (令和 8 年 4 月時点) に更新
      (カシューナッツ義務化, マカダミアナッツ・ピスタチオ追加, まつたけは
      `household-safety` で保持)
- [x] グループ名「芋類」→「やまいも」, 「きのこ」→「まつたけ」リネーム
      (production データ未投入の早期段階で確定)
- [x] `aliases.yaml` に マカダミアナッツ・ピスタチオ canonical 追加,
      やまいも aliases に「とろろ」追加
- [x] `profile/edit.html` でアレルゲンチェックボックスにグループ内訳を
      可視化 (例: 「ナッツ / アーモンド · カシューナッツ · ...」)
- [x] `profile/show.html` の chip にメンバー併記と `title` 属性追加
- [x] `tests/guardrails/test_dictionary_loader.py` に新規 4 ケース追加
      (義務 9 品目, 推奨追加品目, やまいも表記揺れ, まつたけ保護)
- [x] memo/history/013 追加
- [x] **個別食材ベースへの全面移行** (同日追加): グループ一括方式の過剰除去
      問題を解消. `ingredient.allergen_tags` に canonical 自身を含める拡張で
      validator は変更せず, UI を canonical 個別チェックボックス + グループ
      全選択ボタンに刷新 (`partials/_macros.html` に `allergen_checkboxes`
      マクロ新設, `app.js` に `setupAllergenGroupToggles` 追加). 後方互換
      維持. `family.example.yaml` を `[甲殻類]` → `[エビ, カニ]` に migration.
      テスト 4 件追加 (合計 76 passed).

### 検証環境の実 LLM セットアップと開発ログイン (2026-05-29〜2026-07-04)

- [x] `config.py` - `ENV_FILE` 環境変数で `.env.staging` に切替可能に (既定は `.env` のまま)
- [x] `.env.staging` 新設 (実 Vertex `global` + Firestore エミュレータのハイブリッド)
- [x] エミュレータ用ワンクリック開発ログイン (`dev_login_email`/`dev_login_password` +
      Firebase Web SDK ダミー config)
- [x] `firebase.docker.json` 新設 (コンテナ内 0.0.0.0 バインド対応) + `docker-compose.yml` 更新
- [x] memo/history/015 (ヒーロー圧縮残差分), 016 (staging 実 LLM セットアップ) 追加
- [x] GCP IAM 調査: `<作業用Googleアカウント>` にプロジェクトロールが無いことが判明、
      `<管理者Googleアカウント>` から `roles/editor` を付与
- [x] Vertex AI Model Garden で Claude Sonnet 4.6 を Enable
- [ ] Claude Sonnet 4.6 のクォータ増加申請 (審査待ち、`global_online_prediction_requests_per_base_model`)

### LLM プロバイダ抽象化 (Claude / Gemini 切替) (2026-07-04)

- [x] `config.py` - `LLM_PROVIDER` / `LLM_MODEL` / `effective_llm_provider` 追加
      (後方互換: 未指定なら `USE_FAKE_LLM` から導出)
- [x] `llm/client.py` - `VertexGeminiClient` 新設 (`google-genai` SDK, Vertex AI モード)
- [x] `llm/client.py` - `build_raw_client(provider)` をファクトリとして抽出
- [x] `llm/pricing.py` - Gemini 2.5 Flash/Pro 単価追加、未登録モデルの警告ログ
- [x] `evaluation/runner.py` - `_build_llm` 経由に変更、`--live` + fake 解決時に ValueError
- [x] テスト追加 (`test_gemini_client.py`, `test_client_factory.py` 新規 + 既存 3 ファイル拡張)
- [x] `docs/llm-integration.md` §1/§1.1(新設)/§2/§4/§9/§10、`.env.sample`、
      `docs/adr/0005-pluggable-llm-provider.md` 新設
- [x] memo/history/017 追加
- [x] 実 Gemini でのスモークテスト (`evaluation/runner.py --live`)。thinking
      トークンが `max_output_tokens` を消費し JSON が途切れる不具合を発見、
      `thinking_config=ThinkingConfig(thinking_budget=0)` で修正・再検証済み
      (単日提案 8/8 ケース成功)

### ローカル実 LLM 検証と JSON パース失敗リトライ修正 (2026-07-04)

- [x] ローカルで `ENV_FILE=.env.staging LLM_PROVIDER=gemini` により
      `suggest_dinner` を実 Gemini で複数回実行し動作確認
- [x] JSON パース失敗時にリトライされない不具合 (ドキュメントとの乖離) を発見
- [x] `services/suggest_dinner.py` / `services/weekly_planner.py` に
      `_attempt_with_parse_retry()` を追加 (同一プロンプトで 1 回だけ再要求。
      ガードレールリトライとは独立)
- [x] `docs/llm-integration.md` §7 を実装の実態に合わせて修正
- [x] テスト追加 (`test_suggest_dinner.py` / `test_weekly_planner.py` に
      パース失敗→成功、パース失敗2回→例外伝播の各ケース)
- [x] memo/history/018 追加

### ブラウザ実機検証 (apple-container) と ナビ未ログイン表示バグ修正 (2026-07-04)

- [x] apple-container (Docker daemon 不在の代替) で Firestore/Auth エミュレータを起動
- [x] ブラウザで開発ログイン→実 Gemini 提案生成→確認まで一気通貫で動作確認
- [x] `/history` ページでナビが「ログイン」表示になる (実際はログアウトされていない)
      不具合を発見。`history.py` / `proposals.py` (3箇所) が `TemplateResponse` に
      `user` を渡し忘れていたのが原因
- [x] `web/routes/history.py` / `web/routes/proposals.py` を修正
- [x] `tests/integration/test_dashboard_e2e.py` に回帰テスト追加
- [x] memo/history/019 追加

### 実 GCP デプロイ整備 (2026-08-01)

worktree `.claude/worktrees/gcp-deploy` / ブランチ `feature/gcp-deploy-20260801`。

- [x] デプロイ先プロジェクト確定: `<GCP_PROJECT_ID>`
      (表示名 recipe-system, 番号 <GCP_PROJECT_NUMBER>)。環境分離は当面 prod 単一、
      CI は Cloud Build 一本化 (git remote 無しのため手動 submit)
- [x] **Dockerfile のビルド失敗を発見・修正**: `pyproject.toml` の
      `readme = "README.md"` を hatchling が検証するため、builder ステージの
      2 回目 `uv sync` 前に `COPY README.md ./` が必須。未修正では確実に失敗する
      (scratchpad で再現 → 修正後の成功まで確認済み)。runtime ステージの
      dictionaries 重複 COPY も削除
- [x] `scripts/deploy/` 新設 (すべて冪等、`.deploy.env` で環境別上書き):
      `config.sh` / `bootstrap.sh` / `firebase.sh` / `firestore.sh` /
      `deploy.sh` / `backup.sh` / `budget.sh` + `artifact-cleanup-policy.json`
- [x] `cloudbuild.yaml` 全面修正: `SESSION_SECRET` /
      `FIREBASE_WEB_CONFIG_JSON` の Secret Manager 注入 (欠落していたため
      デプロイしても起動時 ValidationError で落ちる状態だった)、実行 SA 指定、
      `LLM_PROVIDER` 明示、`images:` 削除 (push 順序の逆転)、substitutions 外出し
- [x] `firestore.indexes.json` に `meal_plans` (family_id + plan_date) 複合
      インデックス追加 (本番では未作成だと週表示が `FAILED_PRECONDITION`)
- [x] `firestore.rules` に `meal_plans` / `shopping_lists` / `llm_usage` /
      `config` の明示 deny 追加 (SSR 専用コレクションの多層防御)
- [x] `.gcloudignore` 新設 (`.gitignore` が無視されるため `.env` 類と
      `.claude/` を明記)、`.deploy.env.sample`、`.gitignore` 追記
- [x] `.pre-commit-config.yaml` に shellcheck 追加 (`-x`)
- [x] `docs/deployment.md` 新設 (runbook)、`docs/operations.md` §4/§7/§9/§10 更新
      (バックアップは Cloud Scheduler + GCS export → Firestore マネージド
      バックアップ スケジュールに変更)
- [x] memo/history/020 追加
- [ ] スクリプトの実行 (bootstrap → firebase → firestore → deploy) は未実施

## 次フェーズ候補

### Phase 2: 本運用準備

- [ ] `scripts/deploy/` 一式を実行して Cloud Run に初回デプロイ
- [ ] Firebase Console でのサインイン方法有効化・認可済みドメイン追加 (API 未提供)
- [ ] 本番家族データ (`family.production.yaml`) の投入
- [ ] 実 Vertex AI でのゴールデンセット live 評価 (Claude クォータ承認待ち。
      当面は `LLM_PROVIDER=gemini` で代替検証)
- [ ] Cloud Build トリガー (git remote 作成後)
- [ ] Haiku 分担・並列実行 (Sonnet 計画 → Haiku 詳細化)
- [ ] HTMX による週/月 in-place タブ切替 (現状はリンク遷移)
- [ ] shopping_aggregator の単位変換テーブル

## 既知の課題・未対応

- `auth/login.html` と `proposals/detail.html` を Modern Editorial デザイン (Phase F) に合わせて追従させる
- 月間ビュータブのアクティブハイライト調整 (Phase E)
- Cloud CDN / ハッシュ付き静的アセット (Phase F: 現状は FastAPI 直配信)
- Claude Sonnet 4.6 の Vertex AI クォータ増加申請が審査待ち (承認後 `LLM_PROVIDER=claude` に戻す)
- `services/weekly_planner.py` が `llm.generate()` の `max_tokens` を既定値 2000 の
  まま呼んでおり、週間提案 (7 日分 JSON) で出力が途切れる (実 Gemini スモークで
  W001-W003 が JSON デコード失敗となり発覚。Claude でも同様に truncate する可能性が
  高いが未検証)。`docs/llm-integration.md` §2 記載の「5000-6000 目安」に合わせて
  `weekly_planner.py` から明示的に大きい `max_tokens` を渡すよう修正が必要

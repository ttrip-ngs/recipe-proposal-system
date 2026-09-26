# 003: MVP コア実装 (三段階処理・LLM・Repository・Web)

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `002-project-scaffolding.md`

## 概要

Phase 1 MVP の実装本体を実装した。ドメイン → 事前フィルタ → プロンプト → LLM クライアント → 三段階処理 → Repository → Firebase Auth → Web ルーター → Jinja2 テンプレート、の垂直一本を通した。

## 実施内容

### Domain 追加

- `domain/llm_output.py` - `LLMIngredient / LLMDish / LLMProposal / LLMCallMeta`
- `domain/__init__.py` 更新で公開

### Services 層

- `services/filters.py` - `prefilter_recipes()` / `recent_recipe_names()` / `FilterStats`
- `services/prompts.py` - `Prompt` + `get_prompt()` (Jinja2 StrictUndefined)
- `services/suggest_dinner.py` - 三段階処理オーケストレーション
  - `SuggestContext` / `DinnerProposal` / `NormalizedDish` / `LLMResponseParseError`
  - リトライは最大 1 回。2 回目も block 継続なら `succeeded=False` で返す
  - フェイルオープンしない (CLAUDE.md §6.7 遵守)

### プロンプト YAML

- `llm/prompts/suggest_dinner_system.yaml` v1 - アレルゲン絶対禁止・嫌い食材回避・履歴被り回避・JSON 出力スキーマ提示
- `llm/prompts/suggest_dinner_user.yaml` v1 - 候補レシピ・在庫・今日の要望 (cacheable でない部分)

### LLM クライアント

- `llm/client.py` 刷新
  - `LLMClient` Protocol: `generate(system, user, prompt_version, max_tokens) -> LLMResponse`
  - `FakeVertexClient` - `LLMProposal` スキーマに適合する固定 JSON 応答
  - `VertexClaudeClient` - `anthropic[vertex]` SDK 経由の Sonnet 4.6 呼び出し
    - `cache_control={"type": "ephemeral"}` でシステムプロンプトをキャッシュ
    - usage から `input_tokens/output_tokens/cache_read_tokens/cache_creation_input_tokens` を構造化ログに出力
  - `build_client()` - `USE_FAKE_LLM` で切り替え

### Repository 層 (Firestore)

- `family_repository.py` - `get_family()` / `find_family_id_by_email()`
- `recipe_repository.py` - `list_recipes()` (正規化辞書経由で ingredients に allergen_tags 自動付与)
- `history_repository.py` - `recent_history()` / `record_cooked()`
- `proposal_repository.py` - `save_proposal()` / `get_proposal()` / `mark_accepted()`
- `feedback_repository.py` - `save_feedback()` (good/neutral/pass)
- `firestore_client.py` 既存

### Web 層

- `web/middleware/auth.py`
  - `AuthenticatedUser` / `SESSION_KEY`
  - `verify_id_token()` - Firebase Admin SDK で検証
  - `current_user()` - セッション未確立なら 401
  - `require_family()` - allowed_emails 非該当なら 403
- `web/templating.py` - `Jinja2Templates` インスタンス
- `web/routes/`
  - `health.py` - `/healthz`
  - `auth.py` - `/login (GET)` / `/session (POST)` / `/logout (POST)`
  - `home.py` - `/` トップ
  - `proposals.py` - `POST /proposals` (BackgroundTask), `GET /proposals/{id}` (status=pending/ready/error で分岐), `POST /proposals/{id}/accept`, `POST /proposals/{id}/feedback`
  - `profile.py` - `/profile` 閲覧
  - `history.py` - `/history` 閲覧
- `web/templates/`
  - `layout.html` - 共通レイアウト + ミニマル CSS
  - `index.html` - 「献立を提案してもらう」 CTA
  - `proposals/pending.html` - meta refresh 3 秒 (cold start UX)
  - `proposals/detail.html` - 成功/失敗/エラー 3 パターン対応・フィードバックフォーム
  - `proposals/feedback_sent.html` - 送信完了
  - `profile/show.html` - 家族構成・アレルゲン・嫌い食材表示
  - `history/list.html` - 直近 30 日の履歴
  - `auth/login.html` - Firebase SDK 組み込み雛形 + 開発者用手動セッション投入フォーム
  - `errors/403.html` / `404.html` / `500.html`
- `main.py` 刷新 - `create_app()` ファクトリ + `SessionMiddleware` + HTTPException ハンドラ

### テスト追加

- `tests/unit/test_filters.py` - 4 ケース (アレルゲン除外・直近履歴除外・期間判定)
- `tests/unit/test_prompts.py` - 4 ケース (ロード・レンダリング・エラー)
- `tests/unit/test_suggest_dinner.py` - 4 ケース (フェイク成功・リトライ後ブロック継続・リトライ成功・履歴反映)

## 現在のコール全体像 (垂直 1 本)

```
Browser
  └── POST /proposals
      ├── web.routes.proposals.create_proposal
      │   ├── proposal_repository.save_proposal (pending プレースホルダ)
      │   └── BackgroundTasks -> _run_suggestion
      │       ├── family_repository.get_family
      │       ├── recipe_repository.list_recipes (辞書正規化済み)
      │       ├── history_repository.recent_history
      │       ├── services.suggest_dinner
      │       │   ├── services.filters.prefilter_recipes
      │       │   ├── services.prompts.get_prompt (system/user)
      │       │   ├── llm.client.generate (Fake or Vertex)
      │       │   ├── guardrails.dictionary_loader.normalize
      │       │   ├── guardrails.validators.validate_recipe
      │       │   └── (block なら 1 回リトライ)
      │       └── proposals/{id} document .update(status=ready, ...)
      └── RedirectResponse /proposals/{id}
  └── GET /proposals/{id}
      └── templates/proposals/{pending,detail}.html
```

## 設計原則の遵守確認

- CLAUDE.md §6.1 アレルゲン検証は決定論コード: `guardrails/validators.py` のみ
- CLAUDE.md §6.3 プロンプト変更フロー: `llm/prompts/*.yaml` + pre-commit version チェック
- CLAUDE.md §6.6 未知食材ポリシー: 正規化時に allergen_tags が空 → block しない (warn 継続)
- CLAUDE.md §6.7 リトライ戦略: 最大 1 回・フェイルオープン禁止
- `evaluation/runner.py` は services.suggest_dinner 実装後に LLM 実呼び出しと接続可能 (次フェーズ)

## 残課題 (Phase 1 完了までの残り)

1. **evaluation/runner.py を services と接続**: フェイククライアントでゴールデン評価を回す
2. **Firebase Web SDK 連携**: `auth/login.html` を本物の Firebase JS SDK に差し替え
3. **統合テスト**: Firestore エミュレータを docker-compose で起動 → E2E 疎通
4. **uv.lock 生成 + Docker ビルド検証**
5. **README.md** (簡易版)
6. **firebase.json の `firestore.indexes.json` を `firestore:deploy` で流すガイド**
7. **ログ構造化の実装確認**: Cloud Logging のログベース指標を実運用で確認
8. **本番家族プロファイル投入運用の整備**: `family.production.yaml` のテンプレートとレビュー承認フロー

## 参照

- 計画: `~/.claude/plans/recipe-system-design-md-claude-md-curried-salamander.md`
- 前回: `memo/history/002-project-scaffolding.md`
- CLAUDE.md: アレルゲン検証・プロンプト変更フロー・リトライ戦略の遵守事項

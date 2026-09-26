# 002: プロジェクト骨格・設定・ガードレール土台の構築

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `001-initial-docs-setup.md`

## 概要

基本設計ドキュメント完成後、Phase 1 MVP 実装着手のための骨格・設定・ガードレール土台を整備した。LLM サービス層・Web ルーター実装は次回以降。

## 実施内容

### プロジェクト設定ファイル

- `pyproject.toml` - Python 3.12, uv 管理, FastAPI + Pydantic + anthropic[vertex] + Firestore
  - ruff (line-length 100, py312, 強めのルール), mypy (strict), pytest (asyncio_mode=auto) 設定
  - カバレッジ計測対象は `src/recipe_system`
- `.pre-commit-config.yaml` - gitleaks + ruff + pre-commit-hooks 標準 + プロンプト version 必須チェック (ローカルフック)
- `.gitignore` - `.env` / `data/seeds/family.production.yaml` / `evaluation/reports/` / `emulator-data/` を除外
- `.env.sample` - 環境変数テンプレート (13 項目)

### コンテナ・CI

- `Dockerfile` - マルチステージ (builder/runtime), uv 経由、non-root ユーザー, `${PORT}` 対応
- `cloudbuild.yaml` - lint / typecheck / test / build / push / deploy の 6 ステップ
- `.dockerignore` - テスト・ドキュメント・キャッシュを除外

### Firebase 設定

- `firebase.json` - Firestore / Auth / UI エミュレータ (127.0.0.1)
- `firestore.rules` - 家族メンバーのメールホワイトリスト制御、`allowed_emails` ベースの関数 `isAllowedFamilyMember`
- `firestore.indexes.json` - `history / proposals / recipes / feedback` の複合インデックス

### パッケージ骨格 (`src/recipe_system/`)

| モジュール | 責務 |
|---|---|
| `config.py` | Pydantic Settings で環境変数管理 (`get_settings()` lru_cache) |
| `main.py` | FastAPI エントリポイント + `/healthz` |
| `domain/` | `FamilyMember`, `FamilyProfile`, `Ingredient`, `Recipe`, `Violation` (frozen Pydantic モデル) |
| `guardrails/dictionary_loader.py` | 辞書 YAML ロード + 正規化 + `default_dictionary()` |
| `guardrails/validators.py` | `validate_recipe` + `has_blocking_violation` |
| `llm/client.py` | `FakeVertexClient` (固定応答) + `VertexClaudeClient` (スタブ) + `build_client()` |
| `repository/firestore_client.py` | Firestore クライアント初期化 |
| `observability/logging.py` | structlog JSON 出力設定 |

### 食材正規化辞書

- `src/recipe_system/guardrails/dictionaries/aliases.yaml` - 消費者庁 28 品目 + 家庭独自 10 品目
- `src/recipe_system/guardrails/dictionaries/allergens.yaml` - 17 アレルゲングループ

### サンプルデータ

- `data/seeds/family.example.yaml` - 3 メンバー (甲殻類 / 卵+落花生アレルギー例)
- `data/seeds/recipes.example.yaml` - 定番 5 品
- `data/seeds/history.example.yaml` - 直近履歴 3 件

### スクリプト

- `scripts/seed_firestore.py` - `--emulator` / `--project` 対応の投入スクリプト
- `scripts/check_prompt_version.py` - pre-commit 用 version フィールド必須チェック

### 評価ランナー

- `evaluation/runner.py` - JSONL ロード + ゴールデン期待値の静的検査 (MVP 初期版。LLM 実呼び出しは後続で接続)
- `evaluation/golden/allergen_cases.jsonl` - 3 ケース (甲殻類 / 卵 / ナッツ)
- `evaluation/golden/recency_cases.jsonl` - 2 ケース (直近被り / 主菜偏り)
- `evaluation/golden/dislike_and_unknown_cases.jsonl` - 3 ケース (嫌い / 未知食材 / カテゴリ網羅)

### 初期テスト

- `tests/guardrails/test_dictionary_loader.py` - 6 ケース (正規化 / 未知食材 / 消費者庁 8 品目網羅)
- `tests/guardrails/test_validators.py` - 5 ケース (アレルゲン block / 嫌い warn / 複数家族同時検証 / frozen)
- `tests/conftest.py` - `src` パス追加・テスト用環境変数既定値

## 残作業 (次回着手候補)

優先度高:
1. **services 層実装**: 事前フィルタ (アレルゲン除外 + 直近 7 日) + Sonnet 提案 + 事後検証の三段階処理 (`services/suggest_dinner.py`)
2. **Vertex AI クライアントの本実装**: `VertexClaudeClient.suggest_dinner` を `anthropic[vertex]` SDK で実装、Prompt Caching breakpoint 設定
3. **プロンプト YAML の初期版**: `src/recipe_system/llm/prompts/suggest_dinner_system.yaml`, `suggest_dinner_user.yaml`
4. **Repository 実装**: `families` / `recipes` / `history` / `proposals` / `feedback` の Pydantic <-> Firestore 変換
5. **Web ルーター実装**: `/`, `/proposals`, `/proposals/{id}`, `/proposals/{id}/feedback`, `/login`, `/session`
6. **Jinja2 テンプレート**: `layout.html` + `index.html` + `proposals/detail.html` + `proposals/pending.html` の最小版
7. **Firebase Auth ID トークン検証ミドルウェア**: `web/middleware/auth.py`

優先度中:
8. **評価ランナーの LLM 実呼び出し接続**: `services.suggest_dinner` を通して動かす
9. **統合テスト**: Firestore エミュレータを docker-compose で起動 → 疎通確認
10. **README.md**: セットアップ手順の簡易版 (docs/development.md へリンク)
11. **uv.lock 生成 + CI frozen 検証**

## 設計原則の再確認

- アレルゲン検証は決定論コードのみ (CLAUDE.md §6.1)
- プロンプト YAML は version 必須、pre-commit でチェック済み
- 未知食材は warn 継続 (default: `UNKNOWN_INGREDIENT_POLICY=warn`)
- リトライ戦略は最大 1 回、フェイルオープン禁止 (services 実装時に徹底)

## 参照

- 計画: `~/.claude/plans/recipe-system-design-md-claude-md-curried-salamander.md`
- 前回メモ: `memo/history/001-initial-docs-setup.md`

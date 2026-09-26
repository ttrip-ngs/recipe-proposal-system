# 家庭内レシピ提案システム

家族のアレルギー・嫌いな食材・過去履歴を考慮した夕食献立を Claude (Vertex AI) に提案してもらうシステム。アレルゲン検証は決定論的コードで担保する。

詳細な設計は [docs/architecture.md](docs/architecture.md)、Claude Code 向けの作業指針は [CLAUDE.md](CLAUDE.md) を参照。

## クイックスタート (ローカル開発)

### 前提ツール

- `uv` (Python 3.12 管理): `brew install uv`
- `gcloud` CLI: `brew install --cask google-cloud-sdk`
- `firebase-tools`: `npm install -g firebase-tools`
- Java 11+ (Firebase Firestore/Auth エミュレータが要求): `brew install --cask temurin`
- Docker (任意、`docker compose up` で Java 同梱イメージを使う場合に必要)
- `pre-commit`: `uv tool install pre-commit`

### セットアップ

```
uv sync
pre-commit install
gcloud auth application-default login
cp .env.sample .env
```

`.env` を編集して `GOOGLE_CLOUD_PROJECT` を自分のプロジェクト ID に変更する。ローカルは `USE_FAKE_LLM=true` のまま (Vertex AI 課金なし)。

### 起動

別々のターミナルで以下を実行する。

```
firebase emulators:start --only firestore,auth
```

```
uv run python scripts/seed_firestore.py --emulator
uv run uvicorn recipe_system.main:app --reload
```

ブラウザで `http://localhost:8000/login` → 開発者用手動セッション投入 (`/login` の「開発者向け」セクション) でセッション確立 → `/` で「献立を提案してもらう」ボタンを押す。フェイク LLM モードでは固定の和食献立が返る。

## 主要コマンド

### 品質チェック

```
uv run ruff check --fix
uv run ruff format
uv run mypy src
```

### テスト

```
uv run pytest                           # 全テスト
uv run pytest tests/guardrails --cov    # ガードレールのみ (カバレッジ)
```

### 評価

```
uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl
uv run python evaluation/runner.py --golden evaluation/golden/allergen_cases.jsonl --live
```

`--live` は Vertex AI に実呼び出しするので課金される。

### デプロイ (本番)

```
gcloud run deploy recipe-system --source . --region asia-northeast1
```

## プロジェクト構成

```
src/recipe_system/
  domain/         ドメインモデル (Pydantic)
  guardrails/     決定論的アレルゲン検証 + 正規化辞書
  llm/            Vertex AI 経由 Claude クライアント + プロンプト YAML
  repository/     Firestore アクセス層
  services/       三段階処理 (事前フィルタ -> LLM -> ガードレール)
  web/            FastAPI ルーター + Jinja2 テンプレート
  observability/  構造化ログ
data/seeds/       Firestore 初期投入用 YAML (本番データは gitignore)
evaluation/       ゴールデンセット + 評価ランナー
scripts/          運用スクリプト
tests/            unit / integration / guardrails
docs/             基本設計ドキュメント + ADR
memo/history/     開発履歴メモ (連番)
```

## 重要な設計原則

1. **アレルゲン検証は決定論コードで実施**。LLM には渡すが最終承認はコード ([docs/guardrails.md](docs/guardrails.md))
2. **リトライは最大 1 回**、フェイルオープン禁止
3. **未知食材は warn で継続**、週次レビューで辞書追加
4. **プロンプトは `version` フィールド必須**、コミットは `[prompt]` プレフィックス

詳細は [CLAUDE.md](CLAUDE.md) §6。

## ドキュメント

- [docs/architecture.md](docs/architecture.md) - 全体アーキテクチャと Phase 境界
- [docs/development.md](docs/development.md) - ローカル開発環境 (詳細版)
- [docs/data-model.md](docs/data-model.md) - Firestore スキーマ
- [docs/llm-integration.md](docs/llm-integration.md) - LLM 連携と Prompt Caching
- [docs/guardrails.md](docs/guardrails.md) - ガードレール設計
- [docs/evaluation.md](docs/evaluation.md) - 評価・プロンプト版管理
- [docs/operations.md](docs/operations.md) - Cloud Run / Firebase Auth / コスト
- [docs/ui-spec.md](docs/ui-spec.md) - UI 仕様
- [docs/glossary.md](docs/glossary.md) - 用語集
- [docs/adr/](docs/adr/) - 主要な技術選定の意思決定記録

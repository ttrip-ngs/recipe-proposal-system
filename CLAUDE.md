# CLAUDE.md

本ファイルは Claude Code（claude.ai/code）が本リポジトリで作業するための指針を示す。グローバル CLAUDE.md（`~/.claude/CLAUDE.md`）の規約を前提とし、本プロジェクト固有のルールのみを記載する。

## 1. プロジェクト概要

家庭内のレシピ提案・買い物管理を行うシステム。Vertex AI 経由で Claude を用いてレシピ提案を行い、家族のアレルギー・嫌いな食材・過去履歴を踏まえて毎日の献立を生成する。LLM による提案品質と、コードによる決定論的アレルゲン検証の二層構造を最重要原則とする。

## 2. アーキテクチャ要約

本システムは 3 レイヤー構造（ハードルール／強い選好／参考情報）と三段階処理（事前フィルタ → LLM 提案 → 事後検証）で構成される。アレルゲン情報は決定論的なコード検証で守り、LLM はバリエーション生成・季節感・ホリスティック判断を担当する。詳細は [docs/architecture.md](docs/architecture.md) を参照。

## 3. ディレクトリ構造

```
recipe-proposal-system/
├── CLAUDE.md                   # 本ファイル
├── recipe-system-design.md     # 設計ディスカッションの原典
├── pyproject.toml
├── .pre-commit-config.yaml
├── Dockerfile                  # Cloud Run 用イメージ
├── cloudbuild.yaml             # 本番デプロイ (Cloud Build)
├── .github/workflows/          # PR の CI (GitHub Actions) とプロンプト評価
├── src/recipe_system/
│   ├── main.py                 # FastAPI エントリポイント
│   ├── config.py               # 環境変数・設定読込
│   ├── domain/                 # ドメインモデル（Recipe/FamilyMember/Ingredient/Violation）
│   ├── guardrails/             # 決定論的検証
│   │   ├── dictionaries/       # 食材正規化辞書 YAML
│   │   │   ├── aliases.yaml
│   │   │   └── allergens.yaml
│   │   └── validators.py
│   ├── llm/                    # Vertex AI クライアント・プロンプト
│   │   ├── client.py
│   │   └── prompts/            # git 管理のプロンプト YAML（version 必須）
│   ├── repository/             # Firestore アクセス層
│   ├── services/               # ユースケース層（suggest_dinner など）
│   ├── web/                    # FastAPI ルーター・Jinja2 テンプレート
│   │   ├── routes/
│   │   └── templates/
│   └── observability/          # 構造化ログ・メトリクス
├── data/seeds/                 # Firestore 初期投入用サンプル
├── evaluation/
│   ├── golden/                 # ゴールデンセット JSONL
│   ├── runner.py               # 評価ランナー
│   └── reports/                # 評価結果出力（gitignore）
├── scripts/                    # 運用・管理系ワンショットスクリプト
│   └── deploy/                 # GCP 本番デプロイ (bootstrap/firebase/firestore/deploy/backup/budget)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── guardrails/             # アレルゲン検証は独立フォルダ
├── docs/                       # 基本設計ドキュメント
│   ├── architecture.md
│   ├── data-model.md
│   ├── llm-integration.md
│   ├── guardrails.md
│   ├── evaluation.md
│   ├── operations.md
│   ├── ui-spec.md
│   ├── deployment.md
│   ├── development.md
│   ├── glossary.md
│   └── adr/                    # Architecture Decision Records
├── memo/history/               # 開発履歴メモ（連番）
└── tmp/                        # 中間ファイル（gitignore）
```

## 4. ローカル開発環境セットアップ

詳細手順は [docs/development.md](docs/development.md) に集約する。最小限の前提と導入フローのみ以下に記す。

### 前提ツール

- `uv`（Python 3.12 プロジェクト管理）
- `gcloud` CLI（Google Cloud 認証・デプロイ）
- `firebase-tools`（Firestore・Auth エミュレータ）
- Docker（Cloud Run 用イメージビルド）
- `pre-commit`

### 初回セットアップ

```
uv sync
pre-commit install
gcloud auth application-default login
cp .env.sample .env
firebase emulators:start --only firestore,auth
uv run python scripts/seed_firestore.py --emulator
```

環境変数は `.env.sample` に一覧を置き、`.env` は git 管理から除外する（`GOOGLE_CLOUD_PROJECT`、`VERTEX_AI_LOCATION`、`FIRESTORE_EMULATOR_HOST`、`FIREBASE_AUTH_EMULATOR_HOST`、`ANTHROPIC_VERTEX_PROJECT_ID`、`USE_PROMPT_CACHE`、`USE_FAKE_LLM`、`LOG_LEVEL`）。

## 5. 開発コマンド集

### 静的検証

```
uv run ruff check --fix
uv run ruff format
uv run mypy src
```

### テスト

```
uv run pytest
uv run pytest tests/guardrails --cov=src/recipe_system/guardrails
```

### サーバ起動

```
uv run uvicorn recipe_system.main:app --reload
firebase emulators:start --only firestore,auth
```

### データ投入

```
uv run python scripts/seed_firestore.py --emulator
```

### 評価

```
uv run python evaluation/runner.py --golden evaluation/golden/*.jsonl
```

### デプロイ

```
gcloud run deploy recipe-system --source . --region asia-northeast1
```

## 6. 本プロジェクト固有の遵守事項

### 6.1 アレルゲン検証は決定論的コードで実施

アレルゲン検証を LLM（Claude）に委譲する変更は禁止する。理由は [docs/guardrails.md](docs/guardrails.md) に明記してあるが、確率的動作・プロンプトインジェクション耐性なし・テスト困難のため、命に関わるルールは必ずコードで守る。

### 6.2 ガードレール変更には必ずテストを追加

`src/recipe_system/guardrails/` 配下を変更する PR は、必ず `tests/guardrails/` にテストケースを追加する。既存テストを削除・条件緩和してパスさせる運用は厳禁。

### 6.3 プロンプト変更時のフロー

`src/recipe_system/llm/prompts/*.yaml` を編集した場合、以下を遵守する。

- プロンプト YAML の `version` フィールドをインクリメントする
- コミットメッセージに `[prompt]` プレフィックスを付ける
- ゴールデンセット評価（`evaluation/runner.py`）をローカルで実行し、主要メトリクス（`guard_trigger_rate` など）の退行がないことを確認する
- CI では評価を自動実行しない（API 料金のため）。ローカル評価の結果を PR 本文に記載する。CI 上で確認したい場合のみ `.github/workflows/prompt-eval.yml` を手動起動する

### 6.4 Firestore スキーマ変更時のフロー

Firestore のコレクション構造・フィールド追加削除時は以下を同時に行う。

- [docs/data-model.md](docs/data-model.md) を同じ PR で更新する
- `scripts/` 配下に migration スクリプトを追加する
- 本番環境への適用手順を migration スクリプトの docstring に記す

### 6.5 本番家族データの取り扱い

`data/seeds/family.*.yaml` のうち、サンプル（`family.example.yaml`）のみを git 管理し、本番家族プロファイル（`family.production.yaml` など）は git に入れない。アレルギー・健康情報は実質センシティブデータとして扱う。

### 6.6 未知食材ポリシー

LLM 出力に食材正規化辞書に未登録の食材が現れた場合は、`warn` ログに記録した上で提案を継続する。`block` にしない（提案がほぼ全て止まる可能性があるため）。未知食材は週次で人間がレビューし、必要なら `src/recipe_system/guardrails/dictionaries/aliases.yaml` に追加する。

### 6.7 リトライ戦略

ガードレールが違反を検出した場合、LLM 再依頼は最大 1 回。2 回目以降はフェイルオープンせず、UI で「安全な提案を作れませんでした」とユーザーに提示する。無限リトライや自動ルール緩和は絶対に実装しない。

## 7. 参考ドキュメント

- [docs/architecture.md](docs/architecture.md) - 全体アーキテクチャ
- [docs/data-model.md](docs/data-model.md) - Firestore データモデル
- [docs/llm-integration.md](docs/llm-integration.md) - LLM 連携仕様
- [docs/guardrails.md](docs/guardrails.md) - ガードレール設計
- [docs/evaluation.md](docs/evaluation.md) - 評価データセット・プロンプト版管理
- [docs/operations.md](docs/operations.md) - 運用・デプロイ・コスト
- [docs/deployment.md](docs/deployment.md) - 本番デプロイ手順 (runbook)
- [docs/ui-spec.md](docs/ui-spec.md) - UI 仕様
- [docs/development.md](docs/development.md) - ローカル開発環境
- [docs/glossary.md](docs/glossary.md) - 用語集
- [docs/adr/](docs/adr/) - Architecture Decision Records
- [recipe-system-design.md](recipe-system-design.md) - 設計ディスカッション原典

## 8. グローバル規約

絵文字禁止、日本語記述、`memo/history` 連番運用、pre-commit 必須、1 機能 1 ブランチ、根本解決優先（`noqa`・`type: ignore` の安易な使用禁止）などは、グローバル CLAUDE.md（`~/.claude/CLAUDE.md`）の規約に従う。

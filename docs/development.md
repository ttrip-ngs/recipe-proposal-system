# ローカル開発環境

## 1. 前提ツール

以下をローカルマシンに導入しておく。

| ツール | 用途 | インストール例（macOS） |
|---|---|---|
| `uv` | Python 3.12 プロジェクト管理 | `brew install uv` または `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `gcloud` CLI | Google Cloud 認証・デプロイ | `brew install --cask google-cloud-sdk` |
| Node.js 20+ | Firebase CLI の前提 | `brew install node` |
| `firebase-tools` | Firestore/Auth エミュレータ | `npm install -g firebase-tools` |
| **Java 11+** | Firebase Emulator Suite が Firestore/Auth で要求 | `brew install --cask temurin` |
| Docker | docker-compose 版エミュレータ起動 (任意) | `brew install --cask docker` |
| `pre-commit` | コミット時フック | `uv tool install pre-commit` |

Firebase Emulator Suite の Firestore/Auth は Java ランタイム (JRE 11+) を要求する。`firebase emulators:start` 実行時に "Unable to locate a Java Runtime" となる場合は Java 未導入が原因なので、Temurin 等の JDK を導入する。Docker 経由 (`docker compose up`) の場合は `andreysenov/firebase-tools` イメージに Java が同梱されているためローカル Java は不要。

Python 本体は `uv` 管理下に入れる。既存の pyenv・Homebrew Python との競合は `pyproject.toml` の `requires-python = ">=3.12,<3.13"` で固定し、`uv sync` 時に適切なバージョンが自動取得される。

## 2. 初回セットアップ

```
git clone <repo>
cd recipe-proposal-system
uv sync
pre-commit install
gcloud auth login
gcloud auth application-default login
gcloud config set project <dev-project-id>
cp .env.sample .env
firebase login
firebase use <dev-project-id>
firebase emulators:start --only firestore,auth
# 別ターミナルで
uv run python scripts/seed_firestore.py --emulator
uv run uvicorn recipe_system.main:app --reload
```

ブラウザで `http://localhost:8000` にアクセスし、Firebase Auth エミュレータの ID を使ってログインする。

## 3. 環境変数

`.env.sample` に以下を列挙する。`.env` は gitignore。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | - | 使用する GCP プロジェクト ID |
| `VERTEX_AI_LOCATION` | `asia-northeast1` | Vertex AI リージョン |
| `ANTHROPIC_VERTEX_PROJECT_ID` | - | `anthropic[vertex]` 用プロジェクト |
| `FIRESTORE_EMULATOR_HOST` | `127.0.0.1:8080` | Firestore エミュレータ接続先 |
| `FIREBASE_AUTH_EMULATOR_HOST` | `127.0.0.1:9099` | Firebase Auth エミュレータ |
| `USE_PROMPT_CACHE` | `true` | Prompt Caching の有効化 |
| `USE_FAKE_LLM` | `true`（ローカル）/ `false`（本番） | フェイククライアント切替 |
| `LOG_LEVEL` | `INFO` | 構造化ログのレベル |
| `SESSION_SECRET` | - | FastAPI セッション用 |

本番とローカルで差異がある変数は `.env.sample` 内のコメントに明記する。

## 4. Firebase エミュレータ

### 4.1 設定ファイル

- `firebase.json` - ポート・エミュレータ対象を定義
- `firestore.rules` - セキュリティルール（[data-model.md](data-model.md) §4）
- `firestore.indexes.json` - 複合インデックス定義

`firebase.json` 例:

```json
{
  "firestore": {
    "rules": "firestore.rules",
    "indexes": "firestore.indexes.json"
  },
  "emulators": {
    "firestore": {"port": 8080},
    "auth": {"port": 9099},
    "ui": {"enabled": true, "port": 4000}
  }
}
```

### 4.2 起動

```
firebase emulators:start --only firestore,auth
```

エミュレータ UI は `http://localhost:4000` でデータ閲覧・編集可能。

### 4.3 データ永続化

エミュレータ停止時にデータを残すには、起動時に `--import=./emulator-data --export-on-exit=./emulator-data` を付ける。`emulator-data/` は gitignore。

## 5. 初期データ投入

```
uv run python scripts/seed_firestore.py --emulator
```

スクリプトは `data/seeds/` 配下の YAML を読み込み、以下のコレクションに書き込む。

- `families/{familyId}` - 家族プロファイル
- `families/{familyId}/members/{memberId}` - メンバー情報
- `recipes/{recipeId}` - 定番レシピ 100 品

本番環境向けは `--project <prod-id>` を付けて実行する。本番家族プロファイル（`family.production.yaml`）は git 管理外。

## 6. Vertex AI のローカル接続

### 6.1 ADC 設定

```
gcloud auth application-default login
```

これで `~/.config/gcloud/application_default_credentials.json` が作成され、`google-cloud-firestore` と `anthropic[vertex]` が自動で認証情報を拾う。

### 6.2 権限確認

ログインしたアカウントに以下の権限が必要。

- `roles/aiplatform.user`
- `roles/datastore.user`（本番 Firestore に直接触りたい場合）

不足している場合は GCP IAM で一時付与するか、サービスアカウントキーを `GOOGLE_APPLICATION_CREDENTIALS` 環境変数で指定する（キー利用は最終手段）。

### 6.3 フェイク LLM モード

`USE_FAKE_LLM=true` の場合は Vertex AI を呼び出さない。`src/recipe_system/llm/client.py` の `FakeVertexClient` が `evaluation/golden/` のサンプルから決定論的にレスポンスを組み立てる。UI 開発・ユニットテスト時はこちらを使い、Vertex AI 課金を避ける。

## 7. ローカル統合テスト

`docker-compose.yml` を用意し、以下を一発で立ち上げる想定。

```
services:
  firestore-emulator:
    image: google/cloud-sdk:slim
    command: gcloud emulators firestore start --host-port=0.0.0.0:8080
    ports: ["8080:8080"]
  auth-emulator:
    image: ghcr.io/firebase/firebase-tools:latest
    command: firebase emulators:start --only auth
    ports: ["9099:9099"]
```

Python 側は `pytest-docker` でエミュレータ起動を自動化する。

```
uv run pytest tests/integration
```

## 8. トラブルシューティング

### 8.1 ポート競合

- 8000（FastAPI）、8080（Firestore）、9099（Auth）、4000（Emulator UI）
- 他プロセスが使用中の場合は `firebase.json` でポート変更、または `uvicorn --port 8001`

### 8.2 ADC が失効

```
gcloud auth application-default login
```

を再実行する。`USE_FAKE_LLM=true` で運用している場合は影響ない。

### 8.3 エミュレータのデータ消失

起動オプションに `--import --export-on-exit` を付けていない場合は再投入が必要。

```
uv run python scripts/seed_firestore.py --emulator
```

### 8.4 pre-commit 失敗

```
uv run ruff check --fix
uv run ruff format
```

で自動修正した上で再コミット。品質ルールをスキップする `--no-verify` は禁止（CLAUDE.md・グローバル規約）。

## 9. Docker 本番イメージとの差異

### 9.1 Dockerfile

Cloud Run 用のマルチステージビルドを想定。

- ベース: `python:3.12-slim`
- `uv` で依存解決
- 起動コマンド: `uvicorn recipe_system.main:app --host 0.0.0.0 --port ${PORT:-8080}`

### 9.2 ローカルとの差異

| 項目 | ローカル | Cloud Run |
|---|---|---|
| Firestore | エミュレータ | 本番 Firestore |
| Auth | エミュレータ | 本番 Firebase Auth |
| ADC | ユーザー ADC | Cloud Run SA |
| Vertex AI | `USE_FAKE_LLM` で選択 | 本物 |
| ポート | 8000 | `${PORT}`（通常 8080） |

本番デプロイは [operations.md](operations.md) §9。

## 10. IDE 設定

### 10.1 共通

`pyproject.toml` の `[tool.ruff]` と `[tool.mypy]` セクションを正とする。IDE はこれらを自動検出する設定にする。

### 10.2 Neovim

- LSP: `basedpyright` または `pyright`
- フォーマッタ: `ruff-lsp` またはconform.nvim + ruff
- 型チェック: `mypy`（必要に応じて）

### 10.3 VS Code

- 拡張: `ms-python.python`、`charliermarsh.ruff`
- 設定（`.vscode/settings.json` 推奨値）:

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  }
}
```

`.vscode/` は個人設定を含むため一部のみ git 管理する（推奨値は `.vscode/settings.json.sample` として配布も検討）。

## 11. 静的アセット運用

UI は FastAPI の `StaticFiles` で `/static` にマウントしている。

- 物理パス: `src/recipe_system/web/static/{css,js}/`
- マウント定義: `src/recipe_system/main.py` (`app.mount("/static", StaticFiles(...), name="static")`)
- パス定数: `src/recipe_system/web/templating.py` の `STATIC_DIR` を import

### 11.1 CSS / JS の構成

- `static/css/app.css` 単一エントリ（`@layer tokens, base, layout, components, utilities;`）
- `static/js/app.js` 共通スクリプト（progressive enhancement のみ。SSR を壊さない）
- フォントは Google Fonts を `@import` で読込（`Shippori Mincho B1`、`Noto Sans JP`）。CLS 抑制のため `display=swap` + `preconnect`

### 11.2 ローカル開発時のキャッシュ

ブラウザがキャッシュした古い `app.css` を読むと変更が反映されないため、リロード時は DevTools の「Disable cache」を有効にする。本番では Cloud CDN 側でハッシュ付きパス運用が望ましい（将来課題）。

### 11.3 Cloud Run 配信

`Dockerfile` の `COPY src/ /app/src/` で `web/static/` 配下も自動的にイメージへ含まれる。`.dockerignore` には `static` 除外を入れない（既存設定は OK）。FastAPI が直接配信するため CDN 経由ではないが、`prefers-reduced-motion` 尊重 + `Shippori Mincho B1` の subset 読込みでネットワーク負荷は最小限。

### 11.4 視覚検証用プレビューサーバ

`tmp/dev_preview.py`（git 管理外）が auth/Firestore をモックして UI 専用のプレビューサーバを起動する。

```bash
uv run python tmp/dev_preview.py
# http://127.0.0.1:8765/ で全画面を確認可能
playwright-cli goto http://127.0.0.1:8765/
```

ライト/ダーク両モードは `playwright-cli run-code "async page => await page.emulateMedia({ colorScheme: 'dark' })"` で切替。

## 12. 関連ドキュメント

- [operations.md](operations.md) - 本番運用
- [data-model.md](data-model.md) - Firestore スキーマ
- [llm-integration.md](llm-integration.md) - Vertex AI 連携
- [ui-spec.md](ui-spec.md) - デザインシステム（9 章）とモバイル設計（10 章）

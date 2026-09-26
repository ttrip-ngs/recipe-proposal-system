# 025 実 GCP プロジェクトへのデプロイ整備

## 背景

TASKS.md の「Phase 2: 本運用準備」に残っていた「本番 GCP プロジェクト ID 確定・
Cloud Run デプロイ」「Cloud Build / GitHub Actions CI 設定」に着手した。

デザインリファクタ (`feature/full-refactor-bluegray-20260704`) の作業中だった
ため、git worktree (`.claude/worktrees/gcp-deploy`、ブランチ
`feature/gcp-deploy-20260801`) を切って並行で進めた。

## 前提の確定

`gcloud` 認証を通して実プロジェクトを照会した結果、以下を確定した。

- デプロイ先はプロジェクト ID `<GCP_PROJECT_ID>` (表示名 recipe-system、
  プロジェクト番号 <GCP_PROJECT_NUMBER>)。`.env.staging` の指す先と一致する
- 現状 `run` / `cloudbuild` / `artifactregistry` / `secretmanager` / `firestore` /
  `firebase` はいずれも API 未有効、サービスアカウントは Compute default のみ。
  つまり素の状態からの整備になる
- `aiplatform` のみ有効化済み (Vertex AI Model Garden で Claude を Enable 済みのため)
- 環境分離は当面 prod 単一とする。CI は Cloud Build に一本化 (git remote が無いため
  トリガーは設定せず `gcloud builds submit` の手動実行)

なお、ローカルの gcloud 構成プロファイル `recipe-proposal-system` は別プロジェクト
(`neat-bliss-471808-q6`) を指す状態にドリフトしていた。デプロイスクリプトは全ての
`gcloud` 呼出で `--project` を明示し、ambient な設定に依存しないようにした。

## 発見した不具合: Dockerfile がビルドできない

`pyproject.toml` に `readme = "README.md"` があり、hatchling がメタデータ検証時に
実ファイルを読む。一方 Dockerfile の builder ステージはプロジェクト本体を install
する 2 回目の `uv sync --frozen --no-dev` の前に `pyproject.toml` / `uv.lock` /
`src/` しかコピーしていなかったため、ビルドが確実に失敗する状態だった。

scratchpad に同じ構成を再現して確認:

```
OSError: Readme file does not exist: README.md
```

`COPY README.md ./` を追加して解消 (追加後の `uv sync --frozen --no-dev` が成功する
ことを同じ手順で確認)。これまで一度も Cloud Run にデプロイしていなかったため
顕在化していなかった。

あわせて runtime ステージの
`COPY src/recipe_system/guardrails/dictionaries ...` は
`COPY --from=builder /app/src /app/src` と内容が重複していたため削除した。

## 実施した変更

### デプロイスクリプト (`scripts/deploy/`)

いずれも冪等。`.deploy.env` (git 管理外) → 環境変数 → 既定値の順で設定を解決する。

| ファイル | 内容 |
|---|---|
| `config.sh` | 共通設定とヘルパ (`require_gcloud` / `enable_service` / `ensure_service_account` / `grant_project_role`) |
| `bootstrap.sh` | API 有効化、Firestore 作成、Artifact Registry 作成 + 保持ポリシー、実行/ビルド SA と IAM、`session-secret` 生成 |
| `firebase.sh` | Firebase 追加、Web アプリ作成、SDK config を `firebase-web-config` シークレットへ格納 |
| `firestore.sh` | `firestore.rules` / `firestore.indexes.json` の反映 |
| `deploy.sh` | 前提チェック → `gcloud builds submit` → URL 表示と `/healthz` 確認 |
| `backup.sh` | Firestore マネージド バックアップ スケジュール (週次・12 週保持) |
| `budget.sh` | 請求先アカウントに月次予算アラート (1500 円、80%/100%) |

### cloudbuild.yaml

実用に耐えない箇所があったため書き直した。

- `SESSION_SECRET` と `FIREBASE_WEB_CONFIG_JSON` が渡されておらず、デプロイしても
  起動時に `ValidationError` で落ちる状態だった。Secret Manager から `--set-secrets`
  で注入するように修正。特に後者は JSON にカンマを含むため `--set-env-vars` では
  値が壊れる
- `--service-account` (Cloud Run 実行 SA) の指定が無かった
- `LLM_PROVIDER` が渡っておらず、`USE_FAKE_LLM=false` から `claude` に解決されて
  しまう。Claude は Vertex クォータ承認待ちのため当面 `gemini` を明示する
- `images:` は全ステップ終了後に push されるため、deploy ステップの時点で
  Artifact Registry にイメージが無い。明示的な push ステップを deploy の前に置く
  構成に統一し `images:` は削除
- リージョン・リソース値・シークレット名を `substitutions` に外出しし、`deploy.sh`
  から上書きできるようにした

### Firestore

- `firestore.indexes.json` に `meal_plans` (family_id ASC + plan_date ASC) の複合
  インデックスを追加。Phase A で追加した `list_in_range` (等値 + 範囲 + order_by)
  が本番では index 無しで `FAILED_PRECONDITION` になる
- `firestore.rules` に `meal_plans` / `shopping_lists` / `llm_usage` / `config` の
  明示的な deny を追加。これらは SSR (Admin SDK) 専用でクライアント直アクセス経路が
  無いため、将来足したときに既定で開かないようにする

### その他

- `.gcloudignore` 新設。`.gcloudignore` があると gcloud は `.gitignore` を見なく
  なるため、`.env` 類・`.deploy.env`・`.claude/` (worktree を含む) を明記した
- `.deploy.env.sample` 追加、`.gitignore` に `.deploy.env` と `.claude/worktrees/`
- 家族データの除外を `family.production.*` 固定名から
  `family.*.yaml` + `!family.example.yaml` の許可リスト方式に変更
  (CLAUDE.md §6.5 の「production.yaml など」を取りこぼさないため)
- `.pre-commit-config.yaml` に shellcheck を追加 (`-x` で source 追跡)
- `docs/deployment.md` 新設 (runbook)。`docs/operations.md` の §4 (Secret 一覧)、
  §7 (バックアップ方式)、§9 (デプロイフロー)、§10 (環境分離) を実態に合わせて更新

## バックアップ方式の変更

`docs/operations.md` §7 は Cloud Scheduler + GCS export を想定していたが、export の
出力先プレフィクスを毎回変えるために Cloud Function を挟む必要があり、家庭用途には
機構が重い。Firestore のマネージド バックアップ スケジュールなら GCS バケットも
スケジューラも SA も不要で復元も 1 コマンドのため、そちらに置き換えた。長期
アーカイブが必要になった時点で GCS export を足す。

## 残作業

- 実際の `bootstrap.sh` → `firebase.sh` → `firestore.sh` → `deploy.sh` の実行
  (今回はスクリプトと手順書の整備まで)
- Firebase Console での Google サインイン有効化と、デプロイ後の認可済みドメイン追加
  (API 未提供のため手動)
- Claude Sonnet 4.6 の Vertex クォータ承認後に `LLM_PROVIDER=claude` へ切替
- `weekly_planner.py` の `max_tokens` 不足 (TASKS.md 既知課題) はデプロイ前に
  直しておきたい。週間提案が本番で JSON デコード失敗する可能性が高い

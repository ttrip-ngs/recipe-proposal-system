# 本番デプロイ手順 (Cloud Run)

実際の GCP プロジェクトへデプロイするための runbook。設計上の意図は
[operations.md](operations.md)、ローカル開発は [development.md](development.md) を参照する。

## 1. 前提

### 1.1 ツール

| ツール | 用途 |
|---|---|
| `gcloud` | GCP リソース操作・Cloud Build 実行 |
| `firebase` (firebase-tools) | Firebase Auth 設定・Firestore ルール/インデックス反映 |
| `git` | イメージタグに short SHA を使う |
| `openssl` | `SESSION_SECRET` の生成 |

```
gcloud auth login
firebase login
```

### 1.2 対象プロジェクト

| 項目 | 値 |
|---|---|
| プロジェクト ID | `<GCP_PROJECT_ID>` |
| 表示名 | recipe-system |
| プロジェクト番号 | <GCP_PROJECT_NUMBER> |
| リージョン | `asia-northeast1` |

プロジェクト ID がコマンド断片のように見えるが、実在する正規の ID である。

環境は当面 prod 単一で運用する（家族 4 人規模、dev 相当はローカルの Firestore
エミュレータと `.env.staging` の実 LLM 検証で賄う）。dev を足す場合は
`.deploy.env` で `PROJECT_ID` を差し替えれば同じスクリプト一式が使える。

### 1.3 設定の上書き

```
cp .deploy.env.sample .deploy.env
```

`scripts/deploy/config.sh` が `.deploy.env` → 環境変数 → 既定値の順で解決する。
全ての `gcloud` 呼出は `--project` を明示するため、ローカルの gcloud config が
別プロジェクトを指していても誤爆しない。

## 2. 初回セットアップ

上から順に 1 回ずつ実行する。各スクリプトは冪等で、再実行しても既存リソースを
壊さない。

### 2.1 GCP リソース整備

```
./scripts/deploy/bootstrap.sh
```

- API 有効化（run / cloudbuild / artifactregistry / firestore / secretmanager /
  aiplatform / identitytoolkit / firebase / billing / logging / monitoring / storage）
- Firestore データベース作成（`asia-northeast1`、Native モード）
- Artifact Registry `recipe-system` 作成 + 直近 5 バージョン保持ポリシー
- サービスアカウント作成と IAM 付与（§3）
- `session-secret` を生成して Secret Manager に格納

### 2.2 Firebase Authentication

```
./scripts/deploy/firebase.sh
```

- GCP プロジェクトへの Firebase 追加、Web アプリ作成
- SDK config を取得し `firebase-web-config` シークレットへ格納

続けて Console で以下を手動設定する（API が提供されていない）。

1. [サインイン方法](https://console.firebase.google.com/project/<GCP_PROJECT_ID>/authentication/providers)
   で Google とメール/パスワードを有効化
2. 認可済みドメインへの Cloud Run URL 追加は初回デプロイ後（§2.5）

### 2.3 Firestore ルールとインデックス

```
./scripts/deploy/firestore.sh
```

`firestore.rules` と `firestore.indexes.json` を反映する。複合インデックスの
作成はバックグラウンドで進むため、完了前に `meal_plans` の週表示を開くと
`FAILED_PRECONDITION` が出る。数分待ってから確認する。

### 2.4 デプロイ

```
./scripts/deploy/deploy.sh
```

`gcloud builds submit` で lint → typecheck → test → イメージビルド → push →
`gcloud run deploy` まで一気に走る。テストが落ちればデプロイされない。

### 2.5 認可済みドメインの追加

デプロイ後に表示される `https://recipe-system-xxxxx.a.run.app` のホスト名を
[Authentication の設定](https://console.firebase.google.com/project/<GCP_PROJECT_ID>/authentication/settings)
の「承認済みドメイン」に追加する。未追加だとログイン時に
`auth/unauthorized-domain` で失敗する。

### 2.6 初期データ投入

本番家族データは git 管理外（[CLAUDE.md](../CLAUDE.md) §6.5）。手元の
`data/seeds/family.production.yaml` を使って投入する。

```
GOOGLE_CLOUD_PROJECT=<GCP_PROJECT_ID> \
  uv run python scripts/seed_firestore.py --family data/seeds/family.production.yaml
```

`--emulator` を付けないと本番 Firestore に書き込む。`allowed_emails` に
ログインするメールアドレスが含まれていることを必ず確認する。

### 2.7 バックアップと予算アラート

```
./scripts/deploy/backup.sh
./scripts/deploy/budget.sh
```

### 2.8 動作確認

```
curl -fsS "$(gcloud run services describe recipe-system \
  --project=<GCP_PROJECT_ID> --region=asia-northeast1 \
  --format='value(status.url)')/healthz"
```

その後ブラウザで、ログイン → 提案生成 → 確定 → 買い物リスト →
`/admin/usage` にコストが記録されること、までを一通り確認する。

## 3. サービスアカウントと権限

| SA | 用途 | ロール |
|---|---|---|
| `recipe-system-run@` | Cloud Run 実行 | `roles/datastore.user`、`roles/aiplatform.user`、`roles/logging.logWriter`、各シークレットの `secretAccessor` |
| `recipe-system-build@` | Cloud Build 実行 | `roles/cloudbuild.builds.builder`、`roles/run.admin`、実行 SA への `roles/iam.serviceAccountUser` |

シークレットへの参照権限はプロジェクト全体ではなくシークレット単位で付与する。
Vertex AI・Firestore・Firebase Admin SDK はいずれも ADC（実行 SA）で認証するため、
サービスアカウントキーは発行しない。

## 4. 環境変数とシークレット

Cloud Run に渡る設定。`.env` は本番では使わない（イメージにも含めない）。

| 変数 | 渡し方 | 値 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | env | プロジェクト ID |
| `ANTHROPIC_VERTEX_PROJECT_ID` | env | プロジェクト ID |
| `VERTEX_AI_LOCATION` | env | `global`（Claude/Gemini とも検証済み） |
| `LLM_PROVIDER` | env | 当面 `gemini`。Claude クォータ承認後に `claude` |
| `LLM_MODEL` | env | 空なら各クライアントの既定モデル |
| `USE_FAKE_LLM` | env | `false` |
| `USE_PROMPT_CACHE` | env | `true` |
| `UNKNOWN_INGREDIENT_POLICY` | env | `warn`（[CLAUDE.md](../CLAUDE.md) §6.6） |
| `USD_JPY_RATE` | env | `155.0` |
| `LOG_LEVEL` | env | `INFO` |
| `SESSION_SECRET` | Secret Manager `session-secret` | bootstrap.sh がランダム生成 |
| `FIREBASE_WEB_CONFIG_JSON` | Secret Manager `firebase-web-config` | firebase.sh が Firebase から取得 |

`FIREBASE_WEB_CONFIG_JSON` の中身は公開情報だが、JSON にカンマを含むため
`gcloud run deploy --set-env-vars` では値が壊れる。取り回しの都合で Secret
Manager 経由にしている。

`FIRESTORE_EMULATOR_HOST` / `FIREBASE_AUTH_EMULATOR_HOST` / `DEV_LOGIN_*` は
本番では設定しない。設定すると `Settings.is_emulator` が真になり、セッション
Cookie の `https_only` が外れ、開発用ワンクリックログインが有効化されてしまう。

## 5. 通常のデプロイ

```
git commit ...          # イメージタグに short SHA を使うため先にコミットする
./scripts/deploy/deploy.sh
```

未コミット変更があるとタグに `-dirty` が付き、警告が出る。

### 5.1 ロールバック

```
# 直前までのリビジョンを確認
gcloud run revisions list --service=recipe-system \
  --project=<GCP_PROJECT_ID> --region=asia-northeast1

# 特定リビジョンに全トラフィックを戻す
gcloud run services update-traffic recipe-system \
  --to-revisions=REVISION_NAME=100 \
  --project=<GCP_PROJECT_ID> --region=asia-northeast1
```

イメージは Artifact Registry に直近 5 バージョン残るため、タグ指定での再デプロイも可能。

### 5.2 設定だけ変える

```
gcloud run services update recipe-system \
  --update-env-vars=LLM_PROVIDER=claude \
  --project=<GCP_PROJECT_ID> --region=asia-northeast1
```

恒久的な変更は `.deploy.env` にも反映すること（次回 deploy.sh で上書きされるため）。

## 6. バックアップ

Firestore のマネージド バックアップ スケジュール（週次・保持 12 週）を使う。
Cloud Scheduler + GCS export 構成は使わない（[operations.md](operations.md) §7 の
更新理由を参照）。

```
gcloud firestore backups list --location=asia-northeast1 --project=<GCP_PROJECT_ID>
```

復元はバックアップから新しいデータベースを作る形になる。既存 DB は上書きされない。

## 7. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| ビルドが `Readme file does not exist` で失敗 | Dockerfile の builder ステージに `COPY README.md ./` が無い。`pyproject.toml` の `readme` を hatchling が検証するため必須 |
| 起動直後に `SESSION_SECRET` の ValidationError | `--set-secrets` が効いていない。実行 SA に該当シークレットの `secretAccessor` があるか確認 |
| ログイン画面でボタンが無反応 | `FIREBASE_WEB_CONFIG_JSON` 未設定。`firebase.sh` を再実行 |
| `auth/unauthorized-domain` | Firebase の承認済みドメインに Cloud Run URL 未追加（§2.5） |
| 週表示で `FAILED_PRECONDITION` | `meal_plans` の複合インデックス作成待ち。数分待つ |
| 提案時に「今月の AI 予算を超過しました」 | Firestore `config/llm_budget.monthly_jpy_limit` を確認（[operations.md](operations.md) §8.3） |
| `PERMISSION_DENIED` で Vertex AI 呼出が失敗 | 実行 SA の `roles/aiplatform.user`、およびモデルの Enable 状態とクォータを確認 |

ログ:

```
gcloud run services logs read recipe-system \
  --project=<GCP_PROJECT_ID> --region=asia-northeast1 --limit=50
```

## 8. 現時点の制約

- Claude Sonnet 4.6 の Vertex クォータ増加が審査中のため、本番の既定プロバイダは
  `gemini`。承認後に `.deploy.env` の `LLM_PROVIDER` を `claude` に変更する
- CI トリガーは未設定。git remote が無いため `gcloud builds submit` の手動実行のみ。
  GitHub リポジトリを作る場合は Cloud Build トリガー + Workload Identity 連携を別途整備する
- 静的アセットは FastAPI 直配信。Cloud CDN・ハッシュ付きアセットは未対応

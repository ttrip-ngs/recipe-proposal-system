# 運用設計

## 1. Cloud Run 構成

| 項目 | 設定値 |
|---|---|
| サービス名 | `recipe-system` |
| リージョン | `asia-northeast1`（東京） |
| min-instances | `0` |
| max-instances | `3` |
| CPU | `1` |
| memory | `512Mi` |
| concurrency | `20` |
| timeout | `60s` |
| 認証 | `--allow-unauthenticated`（アプリ側で Firebase Auth 検証） |

`min-instances=0` により使わない時間帯の課金を最小化する。家族 4 人の利用頻度ではほぼ常に 0 インスタンスに落ちる想定。

## 2. Cold Start + Vertex AI レイテンシ対策

### 2.1 想定レイテンシ

| 段階 | 時間 |
|---|---|
| Cloud Run cold start | 2〜5 秒 |
| Vertex AI 呼び出し | 3〜5 秒 |
| 合計（最悪） | 10 秒前後 |

### 2.2 UX 対策

- 提案要求時は「考え中」ページを即座に返す
- HTMX polling または SSE で結果を後から差し込む
- 進捗インジケータで 10 秒程度の待ち時間を体感的に緩和する

詳細は [ui-spec.md](ui-spec.md) §6。

### 2.3 Prompt Caching の効果

Prompt Caching 有効時はキャッシュ読み込みで 30〜50% のレイテンシ改善が期待できる。MVP から採用する（[llm-integration.md](llm-integration.md) §4）。

## 3. Firebase Authentication 統合

### 3.1 採用の経緯

既存設計書では Authentik SSO を想定していたが、GCP 内完結・家庭用 4 人規模・Firebase Auth 無料枠（Identity Platform 50,000 MAU）の利点から Firebase Authentication に変更した。Cloud Run との統合もシンプル。

### 3.2 認証方式

- Cloud Run は `--allow-unauthenticated`（IAP・IAM 認証は使わない）
- アプリ側で Firebase Admin SDK を使い、リクエストヘッダの ID トークンを検証する
- 検証後、`request.auth.token.email` をセッションに保持
- `families/{familyId}.allowed_emails` にメールアドレスが含まれる場合のみアクセス許可

### 3.3 サインイン方法

- Google アカウント SSO を MVP のメインフローとする
- メール + パスワードも Firebase Auth で有効化するが、招待のみ許可する運用

### 3.4 家族メンバー追加フロー

- 管理者が Firestore の `families/{familyId}.allowed_emails` にメールを追記
- 対象ユーザーが Google アカウントでサインインするとアクセス可能
- 削除時は `allowed_emails` から当該メールを削除

## 4. Secret Manager

| Secret 名 | 用途 |
|---|---|
| `session-secret` | Starlette `SessionMiddleware` の署名鍵（`SESSION_SECRET`）。`scripts/deploy/bootstrap.sh` がランダム生成する |
| `firebase-web-config` | ログイン画面が埋め込む Firebase Web SDK config（`FIREBASE_WEB_CONFIG_JSON`）。`scripts/deploy/firebase.sh` が Firebase から取得する |

Firebase Admin SDK の SA キーは発行しない。Cloud Run 実行サービスアカウントの ADC で認証できるため、鍵をファイルとして持たない（`web/middleware/auth.py` の `credentials.ApplicationDefault()`）。Vertex AI も同様に実行 SA の `roles/aiplatform.user` で済ませ、API キーは持たない。

`firebase-web-config` の中身自体は公開情報（HTML に埋め込まれる）だが、JSON にカンマを含み `gcloud run deploy --set-env-vars` では値が壊れるため Secret Manager 経由で注入する。

シークレットへの参照権限はプロジェクト全体ではなくシークレット単位で実行 SA に付与する。

## 5. IAM 最小権限設計

### 5.1 Cloud Run 実行 SA

| 権限 | 用途 |
|---|---|
| `roles/datastore.user` | Firestore 読み書き |
| `roles/aiplatform.user` | Vertex AI 呼び出し |
| `roles/secretmanager.secretAccessor` | Secret 読み取り |
| `roles/logging.logWriter` | Cloud Logging 書き込み |

### 5.2 人間ユーザー

開発者は `roles/run.developer` と `roles/datastore.viewer` 程度に留め、本番 Firestore への書き込みはサービス経由に限定する。

### 5.3 Cloud Storage

バックアップバケットは Cloud Run SA・Cloud Scheduler SA の書き込み権限のみ付与し、読み取りは管理者アカウントに制限する。

## 6. Cloud Logging とログベース指標

### 6.1 構造化ログ

[llm-integration.md](llm-integration.md) §8 の必須項目をすべて JSON で Cloud Logging に送る。Python 側は `structlog` を推奨。

### 6.2 ログベース指標

Cloud Logging のカスタム指標を以下で定義する。

| 指標名 | 抽出条件 | 用途 |
|---|---|---|
| `recipe_system/guard_trigger_rate` | `jsonPayload.guard_severity="block"` をカウント | プロンプト品質監視 |
| `recipe_system/warn_rate` | `jsonPayload.guard_severity="warn"` をカウント | 嫌い食材取りこぼし |
| `recipe_system/llm_latency_ms` | `jsonPayload.latency_ms` を distribution | レイテンシ監視 |
| `recipe_system/llm_input_tokens` | `jsonPayload.input_tokens` を distribution | コスト追跡 |

MVP では Cloud Logging の標準画面（Metrics Explorer）で可視化する。Grafana ダッシュボードは Phase 2 以降。

### 6.3 ログ保持

- デフォルト 30 日保持
- 重要イベント（ガード block 発動）は Cloud Storage にエクスポートし長期保管

## 7. Firestore バックアップ

### 7.1 スケジュール

Firestore のマネージド バックアップ スケジュールを使う（週次・日曜）。設定は `scripts/deploy/backup.sh`。

```
gcloud firestore backups schedules create --database='(default)' \
  --recurrence=weekly --day-of-week=SUN --retention=12w
```

当初設計では Cloud Scheduler + GCS export を想定していたが、export の出力先プレフィクスを毎回変えるために Cloud Function を挟む必要があり、家庭用途には機構が重い。マネージド バックアップなら GCS バケットもスケジューラも SA も不要で、復元もコマンド 1 本で済むため置き換えた。長期アーカイブが要るようになった時点で GCS export を追加する。

### 7.2 保持

- 保持 12 週間（週次スケジュールの上限は 14 週間）
- Firestore 側で管理されるため Cloud Storage のライフサイクル設定は不要

### 7.3 復元テスト

- 年 1 回、`--destination-database=restore-test` として別データベースに復元し整合を確認する（既存 DB は上書きされない）
- 確認後は復元先データベースを削除する

## 8. コスト見積と予算アラート

### 8.1 月額試算

| 項目 | 金額 |
|---|---|
| Vertex AI / Claude Sonnet 4.6（夕食のみ 30 回） | 400〜500 円 |
| Prompt Caching 適用後 | 250〜350 円 |
| Cloud Run（min-instances=0） | 0〜100 円 |
| Firestore | 無料枠内 |
| Firebase Auth | 無料枠内 |
| Cloud Storage（バックアップ） | 10〜30 円 |
| Secret Manager | 10 円未満 |
| **合計** | **300〜600 円/月** |

### 8.2 GCP Budget Alert

- 予算: 月 1500 円（目標 600 円に対してバッファ 2.5 倍）
- 通知: 80%（1200 円）、100%（1500 円）で管理者メール
- 設定は `gcloud billing budgets create` または Console で手動

### 8.3 アプリ側ハードキャップ (BudgetGuardedClient)

GCP Billing Alert はあくまで通知であり、アラートが届いた頃には超過済みの
可能性がある。アプリ側にも独立した予算ガード層を設け、二重防御する。

- 実装: `src/recipe_system/llm/budget_guard.py` の `BudgetGuardedClient` が
  `build_client(*, purpose, family_id)` から返るラッパとして全 LLM 呼出を
  intercept する。
- Preflight: 呼出直前に当月の累積コスト + 上限を Firestore から取得し、
  超過していれば API を呼ばずに `BudgetExceededError` を投げる。Firestore
  取得失敗時も block-to-safe で同じ例外を投げる（fail-open しない）。
- 上限値: Firestore `config/llm_budget.monthly_jpy_limit` に保存
  （`scripts/seed_firestore.py` でデフォルト 3000 円を投入）。運用時は
  Firestore コンソールで直接編集する。
- 為替: `USD_JPY_RATE` 環境変数（デフォルト 155.0）で USD→JPY を換算。
- 可視化: `/admin/usage` で当月コスト・日別グラフ・用途別・キャッシュヒット
  率を表示。ダッシュボード TOP にも今月コストウィジェットを常時表示。
- 記録: 全呼出を `llm_usage` Firestore コレクションに 1 件 1 ドキュメントで
  append。Preflight ブロックは API を呼んでいないため記録しない。
- UI: 予算超過時はユーザーに「今月の AI 予算を超過しました。Firestore の
  `config/llm_budget` で上限を上げてください」と提案画面に明示する。

## 9. デプロイフロー

実行手順は [deployment.md](deployment.md) に集約する。ここでは方針のみ記す。

### 9.1 CI/CD の選定

役割で分ける。

| 仕組み | 役割 | 起動 |
|---|---|---|
| GitHub Actions (`.github/workflows/ci.yml`) | PR の品質ゲート: pre-commit 全フック・全履歴 gitleaks・mypy・pytest (Firestore/Auth エミュレータ込み)・ガードレールのカバレッジ 95% 以上・docker build | PR と main / dev への push |
| GitHub Actions (`.github/workflows/prompt-eval.yml`) | ゴールデンセット評価 (実 LLM) | 手動起動のみ (API 料金がかかるため. 通常はローカルで実行) |
| Cloud Build (`cloudbuild.yaml`) | 本番デプロイ: lint → typecheck → test → build → push → deploy | `scripts/deploy/deploy.sh` から手動実行 |

リポジトリは GitHub (public) で管理する。Cloud Build のトリガーは設定せず、デプロイは `gcloud builds submit` の手動実行とする。テストが落ちればデプロイされない。

main / dev はブランチ保護で `ci.yml` の各ジョブ (lint / test / docker build) の通過を必須にしている。Action は SHA 固定で、Dependabot (`.github/dependabot.yml`) が dev 向けに週次で更新 PR を出す。

### 9.2 デプロイコマンド

```
./scripts/deploy/deploy.sh
```

`scripts/deploy/` 配下はいずれも冪等で、`.deploy.env`（git 管理外）で環境ごとの値を上書きできる。全ての `gcloud` 呼出で `--project` を明示するため、ローカルの gcloud config が別プロジェクトを指していても誤爆しない。

### 9.3 Artifact Registry

- リポジトリ名: `recipe-system`
- 保持ポリシー: 直近 5 バージョンのみ残す（`scripts/deploy/artifact-cleanup-policy.json`）

## 10. 環境分離

当面は prod 単一プロジェクトで運用する。

| 環境 | 実体 | 用途 |
|---|---|---|
| local | Firestore / Auth エミュレータ + `USE_FAKE_LLM=true` | 日常の開発 |
| staging 相当 | エミュレータ + 実 LLM（`ENV_FILE=.env.staging`） | 実 LLM の挙動確認 |
| prod | GCP プロジェクト `<GCP_PROJECT_ID>` | 本番 |

家族 4 人規模で常時稼働の dev 環境を持つ意味が薄いため、独立した dev プロジェクトは作らない。必要になった場合は `.deploy.env` の `PROJECT_ID` を差し替えるだけで `scripts/deploy/` 一式がそのまま使える。

- Firestore は環境ごとに独立（ローカルはエミュレータ）
- 本番 Firestore への直接書き込みは管理者のみ

## 11. 個人情報（PII）の取り扱い

家族のアレルギー・健康情報はセンシティブデータとして扱う。

- Vertex AI 経由の Claude 呼び出しは Anthropic 側の学習に使われない契約前提
- Firestore export の Cloud Storage バケットは IAM を最小化
- ログには `member.name` は含めるが、メールアドレス・電話番号等は含めない
- バックアップ復元時はアクセスログを必ず確認する

## 12. 関連ドキュメント

- [architecture.md](architecture.md) - 全体アーキテクチャ
- [deployment.md](deployment.md) - 本番デプロイ手順 (runbook)
- [llm-integration.md](llm-integration.md) - LLM コスト試算詳細
- [development.md](development.md) - ローカル開発との差異
- [ingredient-price-sourcing.md](ingredient-price-sourcing.md) - 食材価格表の月次更新手順

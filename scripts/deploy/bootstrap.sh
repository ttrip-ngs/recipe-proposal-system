#!/usr/bin/env bash
# GCP プロジェクトの初期整備 (冪等).
#
#   API 有効化 → Firestore データベース作成 → Artifact Registry 作成
#   → 実行用 / ビルド用サービスアカウント作成と IAM 付与 → SESSION_SECRET 生成
#
# 何度実行しても既存リソースは変更しない. 実行後は firebase.sh → deploy.sh の順に進む.
# 手順全体は docs/deployment.md を参照.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud

# -----------------------------------------------------------------------------
# 1. API 有効化
# -----------------------------------------------------------------------------
log "API を確認・有効化する"
for api in \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  identitytoolkit.googleapis.com \
  firebase.googleapis.com \
  cloudbilling.googleapis.com \
  billingbudgets.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  storage.googleapis.com
do
  enable_service "${api}"
done

# -----------------------------------------------------------------------------
# 2. Firestore (Native モード)
# -----------------------------------------------------------------------------
if gcloud firestore databases describe --database='(default)' \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "Firestore データベースは作成済み"
else
  log "Firestore データベースを作成する (location=${REGION}, native)"
  gcloud firestore databases create \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --type=firestore-native
fi

# -----------------------------------------------------------------------------
# 3. Artifact Registry
# -----------------------------------------------------------------------------
if gcloud artifacts repositories describe "${AR_REPO}" \
    --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "Artifact Registry ${AR_REPO} は作成済み"
else
  log "Artifact Registry ${AR_REPO} を作成する"
  gcloud artifacts repositories create "${AR_REPO}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --repository-format=docker \
    --description="recipe-system の Cloud Run イメージ"
fi

# 直近 5 バージョンのみ保持 (docs/operations.md §9.3).
log "Artifact Registry のクリーンアップポリシーを適用する"
gcloud artifacts repositories set-cleanup-policies "${AR_REPO}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --policy="$(dirname "${BASH_SOURCE[0]}")/artifact-cleanup-policy.json" \
  --no-dry-run

# -----------------------------------------------------------------------------
# 4. サービスアカウントと IAM
# -----------------------------------------------------------------------------
ensure_service_account "${RUNTIME_SA_ID}" "recipe-system Cloud Run 実行用"
ensure_service_account "${BUILD_SA_ID}" "recipe-system Cloud Build 用"

# 実行 SA: Firestore 読み書き / Vertex AI 呼出 / ログ書込 (docs/operations.md §5.1).
# Secret への参照権限はシークレット単位で付与する (下記 5.).
grant_project_role "${RUNTIME_SA}" roles/datastore.user
grant_project_role "${RUNTIME_SA}" roles/aiplatform.user
grant_project_role "${RUNTIME_SA}" roles/logging.logWriter

# ビルド SA: ビルド実行一式 + Cloud Run へのデプロイ.
grant_project_role "${BUILD_SA}" roles/cloudbuild.builds.builder
grant_project_role "${BUILD_SA}" roles/run.admin

# ビルド SA が実行 SA を Cloud Run に紐付けるための actAs (SA 単位で最小付与).
log "IAM: ${BUILD_SA} が ${RUNTIME_SA} を actAs できるようにする"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${BUILD_SA}" \
  --role=roles/iam.serviceAccountUser \
  --quiet >/dev/null

# 人間ユーザが gcloud builds submit --service-account で ビルド SA を使うための actAs.
ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
log "IAM: ${ACTIVE_ACCOUNT} が ${BUILD_SA} を actAs できるようにする"
gcloud iam service-accounts add-iam-policy-binding "${BUILD_SA}" \
  --project="${PROJECT_ID}" \
  --member="user:${ACTIVE_ACCOUNT}" \
  --role=roles/iam.serviceAccountUser \
  --quiet >/dev/null

# -----------------------------------------------------------------------------
# 5. Secret Manager: SESSION_SECRET
# -----------------------------------------------------------------------------
if gcloud secrets describe "${SECRET_SESSION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "シークレット ${SECRET_SESSION} は作成済み (値は変更しない)"
else
  log "シークレット ${SECRET_SESSION} を作成し、ランダム値を投入する"
  gcloud secrets create "${SECRET_SESSION}" \
    --project="${PROJECT_ID}" --replication-policy=automatic
  openssl rand -base64 48 | tr -d '\n' \
    | gcloud secrets versions add "${SECRET_SESSION}" --project="${PROJECT_ID}" --data-file=-
fi

log "IAM: ${RUNTIME_SA} に ${SECRET_SESSION} の参照権限を付与する"
gcloud secrets add-iam-policy-binding "${SECRET_SESSION}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role=roles/secretmanager.secretAccessor \
  --quiet >/dev/null

# -----------------------------------------------------------------------------
# 完了
# -----------------------------------------------------------------------------
cat <<EOF

$(log "bootstrap 完了")

次の手順:
  1. ./scripts/deploy/firebase.sh   Firebase Auth と Web アプリ設定 (${SECRET_FIREBASE_WEB} を作成)
  2. ./scripts/deploy/firestore.sh  セキュリティルールと複合インデックスを反映
  3. ./scripts/deploy/deploy.sh     Cloud Build → Cloud Run デプロイ

詳細は docs/deployment.md を参照.
EOF

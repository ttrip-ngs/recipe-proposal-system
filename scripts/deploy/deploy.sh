#!/usr/bin/env bash
# Cloud Build を実行して Cloud Run にデプロイする.
#
#   前提チェック → gcloud builds submit (lint/typecheck/test → build → push → deploy)
#   → サービス URL とヘルスチェックの表示
#
# イメージタグには git の short SHA を使う. 未コミット変更がある場合は -dirty を付ける.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud

# -----------------------------------------------------------------------------
# 1. 前提チェック (bootstrap.sh / firebase.sh の完了確認)
# -----------------------------------------------------------------------------
gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 \
  || die "実行 SA ${RUNTIME_SA} が無い. 先に scripts/deploy/bootstrap.sh を実行すること."
gcloud iam service-accounts describe "${BUILD_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 \
  || die "ビルド SA ${BUILD_SA} が無い. 先に scripts/deploy/bootstrap.sh を実行すること."
gcloud secrets describe "${SECRET_SESSION}" --project="${PROJECT_ID}" >/dev/null 2>&1 \
  || die "シークレット ${SECRET_SESSION} が無い. 先に scripts/deploy/bootstrap.sh を実行すること."
gcloud secrets describe "${SECRET_FIREBASE_WEB}" --project="${PROJECT_ID}" >/dev/null 2>&1 \
  || die "シークレット ${SECRET_FIREBASE_WEB} が無い. 先に scripts/deploy/firebase.sh を実行すること."

# -----------------------------------------------------------------------------
# 2. イメージタグ
# -----------------------------------------------------------------------------
IMAGE_TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo manual)"
# 未追跡ファイルもソースとしてアップロードされるため porcelain で一緒に検出する.
if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null)" ]]; then
  warn "未コミットの変更がある. タグに -dirty を付ける (本番運用ではコミットしてから実行すること)."
  IMAGE_TAG="${IMAGE_TAG}-dirty"
fi
log "image tag=${IMAGE_TAG} provider=${LLM_PROVIDER} vertex_location=${VERTEX_AI_LOCATION}"

# -----------------------------------------------------------------------------
# 3. ビルド & デプロイ
# -----------------------------------------------------------------------------
SUBS=(
  "_REGION=${REGION}"
  "_SERVICE=${SERVICE_NAME}"
  "_AR_REPO=${AR_REPO}"
  "_IMAGE_TAG=${IMAGE_TAG}"
  "_RUNTIME_SA=${RUNTIME_SA}"
  "_VERTEX_AI_LOCATION=${VERTEX_AI_LOCATION}"
  "_LLM_PROVIDER=${LLM_PROVIDER}"
  "_USE_PROMPT_CACHE=${USE_PROMPT_CACHE}"
  "_UNKNOWN_INGREDIENT_POLICY=${UNKNOWN_INGREDIENT_POLICY}"
  "_USD_JPY_RATE=${USD_JPY_RATE}"
  "_LOG_LEVEL=${LOG_LEVEL}"
  "_SECRET_SESSION=${SECRET_SESSION}"
  "_SECRET_FIREBASE_WEB=${SECRET_FIREBASE_WEB}"
  "_MIN_INSTANCES=${MIN_INSTANCES}"
  "_MAX_INSTANCES=${MAX_INSTANCES}"
  "_CPU=${CPU}"
  "_MEMORY=${MEMORY}"
  "_CONCURRENCY=${CONCURRENCY}"
  "_TIMEOUT=${TIMEOUT}"
)
# 空値を --substitutions に混ぜない. 未指定なら cloudbuild.yaml の既定 ('') が使われ、
# 各 LLM クライアントが自前の DEFAULT_MODEL にフォールバックする.
if [[ -n "${LLM_MODEL}" ]]; then
  SUBS+=("_LLM_MODEL=${LLM_MODEL}")
fi

# シェル配列をカンマ区切りに畳む (値にカンマを含む設定は無い).
SUBS_JOINED="$(IFS=','; echo "${SUBS[*]}")"

gcloud builds submit "${REPO_ROOT}" \
  --project="${PROJECT_ID}" \
  --config="${REPO_ROOT}/cloudbuild.yaml" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --substitutions="${SUBS_JOINED}"

# -----------------------------------------------------------------------------
# 4. 確認
# -----------------------------------------------------------------------------
SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

log "デプロイ完了: ${SERVICE_URL}"
log "ヘルスチェック"
if curl -fsS "${SERVICE_URL}/healthz"; then
  echo
else
  warn "/healthz に到達できなかった. 'gcloud run services logs read ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION}' で起動ログを確認すること."
fi

cat <<EOF

初回デプロイ時は続けて以下を行うこと.

  1. Firebase の認可済みドメインに ${SERVICE_URL#https://} を追加
     https://console.firebase.google.com/project/${PROJECT_ID}/authentication/settings
  2. 本番家族データの投入 (--family を省略するとサンプル家族が本番に入るので必ず指定する)
     GOOGLE_CLOUD_PROJECT=${PROJECT_ID} uv run python scripts/seed_firestore.py \\
       --family data/seeds/family.production.yaml
  3. ログ確認
     gcloud run services logs read ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --limit=50
EOF

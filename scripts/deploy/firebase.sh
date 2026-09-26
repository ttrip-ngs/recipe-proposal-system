#!/usr/bin/env bash
# Firebase (Authentication + Web アプリ) の初期整備 (冪等).
#
#   GCP プロジェクトへの Firebase 追加 → Web アプリ作成 → SDK config 取得
#   → Secret Manager (${SECRET_FIREBASE_WEB}) へ格納
#
# FIREBASE_WEB_CONFIG_JSON は JSON にカンマを含むため gcloud run deploy の
# --set-env-vars では正しく渡せない. 値自体は公開情報だが、取り回しの都合で
# Secret Manager 経由で注入する (docs/deployment.md §4 参照).
#
# Google サインイン プロバイダの有効化と認可ドメイン追加は API 化されていないため、
# 本スクリプトの最後に Console 手順を表示する.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud
command -v firebase >/dev/null 2>&1 \
  || die "firebase CLI が見つからない. 'npm i -g firebase-tools' で導入すること."
if firebase login:list 2>/dev/null | grep -q "No authorized accounts"; then
  die "firebase にログインしていない. 'firebase login' を実行すること."
fi

# -----------------------------------------------------------------------------
# 1. GCP プロジェクトに Firebase を追加
# -----------------------------------------------------------------------------
if firebase projects:list --json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(p["projectId"] for p in d.get("result", [])))' \
    | grep -qx "${PROJECT_ID}"; then
  log "Firebase は ${PROJECT_ID} に追加済み"
else
  log "Firebase を ${PROJECT_ID} に追加する"
  firebase projects:addfirebase "${PROJECT_ID}"
fi

# -----------------------------------------------------------------------------
# 2. Web アプリ
# -----------------------------------------------------------------------------
APP_ID="$(firebase apps:list WEB --project "${PROJECT_ID}" --json \
  | python3 -c 'import json,sys
apps = json.load(sys.stdin).get("result") or []
print(apps[0]["appId"] if apps else "")')"

if [[ -z "${APP_ID}" ]]; then
  log "Firebase Web アプリを作成する"
  firebase apps:create WEB "${SERVICE_NAME}" --project "${PROJECT_ID}"
  APP_ID="$(firebase apps:list WEB --project "${PROJECT_ID}" --json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["appId"])')"
fi
[[ -n "${APP_ID}" ]] || die "Web アプリの appId を取得できなかった."
log "Web アプリ appId=${APP_ID}"

# -----------------------------------------------------------------------------
# 3. SDK config を Secret Manager へ
# -----------------------------------------------------------------------------
# アプリ側 (config.firebase_web_config) が必要とするキーだけに絞って格納する.
WEB_CONFIG="$(firebase apps:sdkconfig WEB "${APP_ID}" --project "${PROJECT_ID}" --json \
  | python3 -c 'import json,sys
cfg = json.load(sys.stdin)["result"]["sdkConfig"]
keys = ("apiKey", "authDomain", "projectId", "appId", "storageBucket", "messagingSenderId")
print(json.dumps({k: cfg[k] for k in keys if k in cfg}, ensure_ascii=False, separators=(",", ":")))')"
[[ -n "${WEB_CONFIG}" ]] || die "Firebase SDK config を取得できなかった."

if ! gcloud secrets describe "${SECRET_FIREBASE_WEB}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "シークレット ${SECRET_FIREBASE_WEB} を作成する"
  gcloud secrets create "${SECRET_FIREBASE_WEB}" \
    --project="${PROJECT_ID}" --replication-policy=automatic
fi

# 既存の最新バージョンと同じ内容なら version を増やさない.
CURRENT="$(gcloud secrets versions access latest --secret="${SECRET_FIREBASE_WEB}" \
  --project="${PROJECT_ID}" 2>/dev/null || true)"
if [[ "${CURRENT}" == "${WEB_CONFIG}" ]]; then
  log "${SECRET_FIREBASE_WEB} は最新の内容と一致 (更新不要)"
else
  log "${SECRET_FIREBASE_WEB} に新しいバージョンを追加する"
  printf '%s' "${WEB_CONFIG}" \
    | gcloud secrets versions add "${SECRET_FIREBASE_WEB}" --project="${PROJECT_ID}" --data-file=-
fi

log "IAM: ${RUNTIME_SA} に ${SECRET_FIREBASE_WEB} の参照権限を付与する"
gcloud secrets add-iam-policy-binding "${SECRET_FIREBASE_WEB}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role=roles/secretmanager.secretAccessor \
  --quiet >/dev/null

# -----------------------------------------------------------------------------
# 4. Console でしか行えない設定を案内
# -----------------------------------------------------------------------------
cat <<EOF

$(log "firebase 整備完了")

Console で以下を手動設定すること (API 未提供).

  1. サインイン方法の有効化
     https://console.firebase.google.com/project/${PROJECT_ID}/authentication/providers
     - Google を有効化 (docs/operations.md §3.3 のメインフロー)
     - メール / パスワードを有効化 (招待運用)

  2. 認可済みドメインに Cloud Run の URL を追加
     https://console.firebase.google.com/project/${PROJECT_ID}/authentication/settings
     - deploy.sh 実行後に表示される *.run.app のホスト名を追加する
       (未追加だと signInWithPopup が auth/unauthorized-domain で失敗する)
EOF

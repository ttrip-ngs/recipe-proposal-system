#!/usr/bin/env bash
# デプロイ系スクリプトの共通設定.
#
# 各スクリプトの先頭で source する. 値は次の優先順で解決する.
#   1. 実行時の環境変数         (例: PROJECT_ID=... ./scripts/deploy/deploy.sh)
#   2. リポジトリルートの .deploy.env  (git 管理外. 環境ごとの上書き用)
#   3. 本ファイルの既定値
#
# 全ての gcloud 呼出で --project を明示するため、ローカルの gcloud config が
# 別プロジェクトを指していても誤爆しない.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ -f "${REPO_ROOT}/.deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.deploy.env"
fi

# --- 対象プロジェクト -------------------------------------------------------
# 公開リポジトリに実 ID を置かないため既定値は持たない.
# .deploy.env (git 管理外) か環境変数で必ず指定する.
PROJECT_ID="${PROJECT_ID:?PROJECT_ID を .deploy.env か環境変数で指定してください}"
REGION="${REGION:-asia-northeast1}"

# --- Cloud Run / Artifact Registry ------------------------------------------
SERVICE_NAME="${SERVICE_NAME:-recipe-system}"
AR_REPO="${AR_REPO:-recipe-system}"

# --- サービスアカウント ------------------------------------------------------
RUNTIME_SA_ID="${RUNTIME_SA_ID:-recipe-system-run}"
BUILD_SA_ID="${BUILD_SA_ID:-recipe-system-build}"
# 各スクリプトから参照するため export する (source 元でしか使われない値).
export RUNTIME_SA="${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
export BUILD_SA="${BUILD_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

# --- Secret Manager ----------------------------------------------------------
SECRET_SESSION="${SECRET_SESSION:-session-secret}"
SECRET_FIREBASE_WEB="${SECRET_FIREBASE_WEB:-firebase-web-config}"

# --- アプリ設定 (Cloud Run の環境変数として渡す) ------------------------------
# Vertex AI の Claude / Gemini は global エンドポイントで検証済みのため既定は global.
# asia-northeast1 に固定したい場合は .deploy.env で上書きする.
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-global}"
# Claude Sonnet 4.6 は Vertex クォータ承認待ちのため、当面 gemini を既定とする.
# 承認後は .deploy.env で LLM_PROVIDER=claude に切り替える.
LLM_PROVIDER="${LLM_PROVIDER:-gemini}"
LLM_MODEL="${LLM_MODEL:-}"
USE_PROMPT_CACHE="${USE_PROMPT_CACHE:-true}"
UNKNOWN_INGREDIENT_POLICY="${UNKNOWN_INGREDIENT_POLICY:-warn}"
USD_JPY_RATE="${USD_JPY_RATE:-155.0}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

# --- Cloud Run リソース (docs/operations.md §1 準拠) --------------------------
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-3}"
CPU="${CPU:-1}"
MEMORY="${MEMORY:-512Mi}"
CONCURRENCY="${CONCURRENCY:-20}"
TIMEOUT="${TIMEOUT:-60s}"

# --- バックアップ (docs/operations.md §7) -------------------------------------
# Firestore マネージド バックアップ スケジュールを使う (GCS export + Cloud Scheduler は不要).
BACKUP_RECURRENCE="${BACKUP_RECURRENCE:-weekly}"
BACKUP_DAY_OF_WEEK="${BACKUP_DAY_OF_WEEK:-SUN}"
# weekly スケジュールの保持期間は最大 14w.
BACKUP_RETENTION="${BACKUP_RETENTION:-12w}"

# --- 予算アラート (docs/operations.md §8.2) ----------------------------------
BUDGET_JPY="${BUDGET_JPY:-1500}"
BUDGET_DISPLAY_NAME="${BUDGET_DISPLAY_NAME:-recipe-system-monthly}"
BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID:-}"

# --- ヘルパ ------------------------------------------------------------------

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# gcloud / 認証済みアカウント / プロジェクト到達性をまとめて確認する.
require_gcloud() {
  command -v gcloud >/dev/null 2>&1 || die "gcloud が見つからない. Google Cloud SDK を導入すること."
  local account
  account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  [[ -n "${account}" ]] || die "gcloud にログインしていない. 'gcloud auth login' を実行すること."
  gcloud projects describe "${PROJECT_ID}" --format='value(projectId)' >/dev/null \
    || die "プロジェクト ${PROJECT_ID} にアクセスできない (アカウント: ${account})."
  log "project=${PROJECT_ID} region=${REGION} account=${account}"
}

# 指定 API が未有効なら有効化する (有効なら何もしない).
enable_service() {
  local api="$1"
  if gcloud services list --enabled --project="${PROJECT_ID}" \
      --filter="config.name=${api}" --format='value(config.name)' | grep -q .; then
    return 0
  fi
  log "API 有効化: ${api}"
  gcloud services enable "${api}" --project="${PROJECT_ID}"
}

# サービスアカウントが無ければ作る.
ensure_service_account() {
  local sa_id="$1" display="$2"
  if gcloud iam service-accounts describe "${sa_id}@${PROJECT_ID}.iam.gserviceaccount.com" \
      --project="${PROJECT_ID}" >/dev/null 2>&1; then
    return 0
  fi
  log "サービスアカウント作成: ${sa_id}"
  gcloud iam service-accounts create "${sa_id}" \
    --project="${PROJECT_ID}" --display-name="${display}"
}

# プロジェクトレベルの IAM バインドを付与する (add-iam-policy-binding は冪等).
grant_project_role() {
  local member="$1" role="$2"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${member}" --role="${role}" \
    --condition=None --quiet >/dev/null
  log "IAM: ${member} += ${role}"
}

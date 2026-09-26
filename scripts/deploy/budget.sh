#!/usr/bin/env bash
# GCP 請求先アカウントに月次予算アラートを作成する (冪等).
#
# docs/operations.md §8.2 の「月 1500 円 / 80% / 100% 通知」を実体化する.
# これは通知のみ. 実際に LLM 呼出を止めるのはアプリ側の BudgetGuardedClient
# (docs/operations.md §8.3) であり、両者は独立した二重防御である.
#
# 実行には請求先アカウントに対する billing.budgets.create 権限が必要
# (roles/billing.admin など). 権限が無い場合は Console から手動作成すること.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud

if [[ -z "${BILLING_ACCOUNT_ID}" ]]; then
  BILLING_ACCOUNT_NAME="$(gcloud billing projects describe "${PROJECT_ID}" \
    --format='value(billingAccountName)' 2>/dev/null || true)"
  [[ -n "${BILLING_ACCOUNT_NAME}" ]] \
    || die "請求先アカウントを取得できない. .deploy.env に BILLING_ACCOUNT_ID を設定すること."
  BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_NAME#billingAccounts/}"
fi
log "billing account=${BILLING_ACCOUNT_ID}"

if gcloud billing budgets list --billing-account="${BILLING_ACCOUNT_ID}" \
    --filter="displayName=${BUDGET_DISPLAY_NAME}" --format='value(name)' 2>/dev/null | grep -q .; then
  log "予算 ${BUDGET_DISPLAY_NAME} は作成済み (金額変更は Console か budgets update で行う)"
  exit 0
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

log "予算 ${BUDGET_DISPLAY_NAME} を作成する (月 ${BUDGET_JPY} 円, 80%/100% で通知)"
gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --display-name="${BUDGET_DISPLAY_NAME}" \
  --budget-amount="${BUDGET_JPY}JPY" \
  --filter-projects="projects/${PROJECT_NUMBER}" \
  --threshold-rule=percent=0.8 \
  --threshold-rule=percent=1.0

log "予算アラート作成完了 (通知先は請求先アカウントの管理者メール)"

#!/usr/bin/env bash
# Firestore のマネージド バックアップ スケジュールを設定する (冪等).
#
# docs/operations.md §7 では Cloud Scheduler + GCS export を想定していたが、
# Firestore 標準のバックアップ スケジュールで同等以上のことが賄えるため
# そちらを採用する (Cloud Function も GCS バケットも不要、復元も 1 コマンド).
# 長期アーカイブが必要になった場合のみ GCS export を追加する.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud

EXISTING="$(gcloud firestore backups schedules list --database='(default)' \
  --project="${PROJECT_ID}" --format='value(name)' 2>/dev/null || true)"

if [[ -n "${EXISTING}" ]]; then
  # 存在確認のみで、頻度・保持期間が config.sh の値と一致するかまでは照合しない.
  # 変更したい場合は 'gcloud firestore backups schedules update' を使う.
  log "バックアップ スケジュールは設定済み (現在の設定を表示する)"
  gcloud firestore backups schedules list --database='(default)' --project="${PROJECT_ID}"
  exit 0
fi

log "バックアップ スケジュールを作成する (${BACKUP_RECURRENCE}/${BACKUP_DAY_OF_WEEK}, 保持 ${BACKUP_RETENTION})"
gcloud firestore backups schedules create \
  --project="${PROJECT_ID}" \
  --database='(default)' \
  --recurrence="${BACKUP_RECURRENCE}" \
  --day-of-week="${BACKUP_DAY_OF_WEEK}" \
  --retention="${BACKUP_RETENTION}"

cat <<EOF

$(log "バックアップ設定完了")

バックアップ一覧:
  gcloud firestore backups list --location=${REGION} --project=${PROJECT_ID}

復元 (新しいデータベースとして復元される. 既存 DB は上書きされない):
  gcloud firestore databases restore \\
    --source-backup=projects/${PROJECT_ID}/locations/${REGION}/backups/BACKUP_ID \\
    --destination-database=restore-test --project=${PROJECT_ID}
EOF

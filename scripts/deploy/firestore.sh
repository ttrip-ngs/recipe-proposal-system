#!/usr/bin/env bash
# Firestore のセキュリティルールと複合インデックスを本番プロジェクトへ反映する.
#
# 反映元は firebase.json が指す firestore.rules / firestore.indexes.json.
# アプリ本体は Admin SDK 経由 (ルールをバイパス) で動くため、ルールは
# クライアント直アクセスに対する多層防御として維持する.

# shellcheck source=scripts/deploy/config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

require_gcloud
command -v firebase >/dev/null 2>&1 \
  || die "firebase CLI が見つからない. 'npm i -g firebase-tools' で導入すること."

log "現在のインデックス一覧 (反映前)"
gcloud firestore indexes composite list --project="${PROJECT_ID}" \
  --format='table(name.basename(),collectionGroup,fields.len())' 2>/dev/null || true

log "firestore.rules と firestore.indexes.json を ${PROJECT_ID} に反映する"
(cd "${REPO_ROOT}" && firebase deploy --only firestore:rules,firestore:indexes --project "${PROJECT_ID}")

cat <<EOF

$(log "Firestore 反映完了")

複合インデックスの作成はバックグラウンドで進む. 進捗確認:
  gcloud firestore indexes composite list --project=${PROJECT_ID}
EOF

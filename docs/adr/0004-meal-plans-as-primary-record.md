# ADR 0004: meal_plans を「献立予定」の真実のソースとして導入

- Status: Accepted
- Date: 2026-05-24

## Context

MVP 当初のデータモデルでは、`proposals` ドキュメントが「LLM 提案ログ」と「採用された献立」の両方を兼ねていた (`accepted` フィールドで採用フラグを立てる)。`history` は別コレクションとして「作った料理」をテキストで持つ補助的な記録だった。

しかし以下の進化要件が出てきた:

- TOP ページをダッシュボード化し、カレンダー風に週/月単位で献立を俯瞰したい
- 1 日 1 食 (夕食) の予定を「未定 / 提案中 / 確定 / 調理済 / スキップ」という状態で管理したい
- 将来的に週間献立まとめ提案・買い物リスト・献立の手動入力などを TOP から触れるようにしたい

`proposals` ベースのままだと:

- 「LLM 呼び出しログ」と「カレンダーの 1 セル」が同じドキュメントになり責務が混ざる
- 日付概念がないため週/月ビューでの突き合わせがしづらい
- 手動入力や複数提案からのスワップに `accepted` フラグだけでは対応しきれない
- LLM 呼び出ししていない予定 (skip / 手動入力) を表現できない

## Decision

新コレクション **`meal_plans`** を導入し、これを「家族 x 日付 x 夕食枠」の真実のソースとする。

- ドキュメント ID: `{family_id}_{YYYY-MM-DD}` (JST 基準)
- 1 日 1 ドキュメント (slot=`"dinner"` 固定。将来 lunch / breakfast 追加用に slot フィールドを持つが Phase A では dinner のみ)
- 状態: `empty` / `proposed` / `confirmed` / `cooked` / `skipped`
- LLM 提案を採用した場合は `proposal_id` で `proposals` ドキュメントを参照
- `dishes` には採用/提案時点のスナップショットを凍結保存

`proposals` は「LLM 呼び出しの完全ログ」(プロンプト改善・評価データ・監査用) として責務を絞る。`accepted` フィールドは互換維持のため残すが、最終的な「採用済み」判定は `meal_plans.status in {confirmed, cooked}` を使う。

`history` は段階的廃止対象。Phase A では既存データを `migrate_history_to_meal_plans.py` で `meal_plans` に backfill する。移行期間中は読み取り側だけ残し、最終的に削除する。

## Consequences

### 正の影響

- カレンダー UI 描画が「家族 + 日付範囲」のシンプルなクエリで済む
- 1 日 1 ドキュメント制約を Firestore 側で担保できる (ID が一意キー)
- 「未定」「スキップ」など LLM 呼び出しを伴わない状態を自然に表現できる
- 週間まとめ提案・買い物リスト集約・手動入力など将来機能を `meal_plans` 上に統一的に乗せられる
- `proposals` が純粋なログになるため、評価データセット (`evaluation/golden/`) との対応が明確になる

### 負の影響

- コレクションが 1 つ増え、移行スクリプトの運用が必要
- Firestore セキュリティルールに `meal_plans` 用エントリを追加する必要がある
- インデックスを増やす必要がある (`family_id ASC, plan_date ASC`)

### リスクと緩和策

- **マイグレーション失敗**: `migrate_history_to_meal_plans.py` は dry-run デフォルト + `--apply` で実書き込み、`get_meal_plan` で衝突回避し idempotent に動作する
- **proposals ↔ meal_plans の同期ずれ**: 書き込みは web ルート (`routes/plans.py`) でトランザクション的に両者を更新する。バックグラウンドタスク失敗時は `meal_plans.status` を `empty` に戻す

## 参照

- [data-model.md](../data-model.md) §2
- [ui-spec.md](../ui-spec.md) §2 §3
- [architecture.md](../architecture.md) §4
- 設計プラン: `.claude/plans/top-image-3-agile-corbato.md`

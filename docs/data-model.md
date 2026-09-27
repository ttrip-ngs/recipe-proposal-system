# データモデル

## 1. 設計方針

データ永続化には Firestore Native Mode を採用する。Markdown + Git による管理は初期投入・サンプル配布の用途に限定し、マスタデータは Firestore に集約する。採用経緯は [adr/0001-markdown-to-firestore.md](adr/0001-markdown-to-firestore.md) を参照。

- Firestore を真実のソースとする
- `data/seeds/` に Markdown/YAML のサンプルデータを置き、エクスポート・インポート機能で退路を確保する
- 家庭内 1 世帯の規模では Firestore 無料枠に収まる

## 2. コレクション設計

### families / {familyId}

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| name | string | 必須 | 家族名（例: 「高荒家」） |
| timezone | string | 必須 | 既定 `Asia/Tokyo` |
| allowed_emails | array<string> | 必須 | 家族メンバーのメールホワイトリスト |
| created_at | timestamp | 必須 | 作成日時 |
| updated_at | timestamp | 必須 | 更新日時 |

### families / {familyId} / members / {memberId}

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| name | string | 必須 | 呼称 |
| role | string | 任意 | `adult` / `child` など |
| allergens | array<string> | 必須 | アレルゲン（canonical 名またはグループ名。例: `エビ`、`卵`） |
| item_policies | map<string, map<string, string>> | 任意 | 通常は除去不要な食品の可/不可。`{アレルゲン: {食品: "allow" \| "block"}}`（例: `{"大豆": {"醤油": "allow", "味噌": "block"}}`）。未設定の食品は除去扱い（ADR 0007） |
| dislikes | array<string> | 任意 | 嫌いな食材（canonical 名） |
| likes | array<string> | 任意 | 好きな食材 |
| notes | string | 任意 | 医療上の注意、補足 |
| reviewed_at | timestamp | 必須 | アレルギー情報の最終レビュー日 |

### recipes / {recipeId}

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| name | string | 必須 | 料理名 |
| main_ingredient | string | 必須 | 主食材 canonical 名 |
| ingredients | array<map> | 必須 | `{name, canonical, allergen_tags, quantity, unit}` の配列 |
| steps | array<string> | 必須 | 手順 |
| category | string | 必須 | `主菜` / `副菜` / `汁物` |
| tags | array<string> | 任意 | `和食`、`低糖質` など |
| servings | integer | 必須 | 既定 4 人分 |
| source | string | 任意 | 参照元（本・URL） |
| created_at | timestamp | 必須 | |

### shopping_lists / {listId}

週次の買い物リスト。ドキュメント ID は `{family_id}_{ISO_week}` (例 `family-1_2026-W22`) で固定し、家族 x 週で一意。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| family_id | string | 必須 | |
| week_start | string | 必須 | `YYYY-MM-DD` (月曜) |
| week_end | string | 必須 | `YYYY-MM-DD` (日曜) |
| iso_week | string | 必須 | `YYYY-Www` (重複インデックス用) |
| items | array<map> | 必須 | `{item_id, canonical, raw_names[], amounts[{quantity, unit}], pantry, checked, manual, note}` |
| generated_at | timestamp | 必須 | 集約実行時刻 |
| updated_at | timestamp | 必須 | チェック状態などの変更時刻 |

集約規則は `services/shopping_aggregator.py` 参照。

- `canonical` は買い物用の正規化名。アレルゲン辞書の canonical (醤油 -> 大豆 など) ではなく、食材名を `services/shopping_dictionary.yaml` で正規化したもの
- 1 食材 1 行。`amounts` に単位ごとの合算数量を並べる (大さじ/小さじ/カップ/cc/L は ml、kg は g に換算して合算)
- 水・お湯などは載せない。調味料などの常備品は `pantry=true` とし、画面では「常備品」欄に分けて残り件数に数えない
- 2026-09 に `total_quantity` / `unit` から `amounts` / `pantry` へ変更。移行は `scripts/migrate_shopping_items_amounts.py` (旧形式も読込可能)

### meal_plans / {planId}

家族 x 日付 x 夕食枠の予定。ドキュメント ID は `{family_id}_{YYYY-MM-DD}` で固定し、ユニーク制約を Firestore 側で担保する。状態遷移とカレンダー描画の真実のソース。詳細は [adr/0004-meal-plans-as-primary-record.md](adr/0004-meal-plans-as-primary-record.md)。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| family_id | string | 必須 | |
| plan_date | string | 必須 | `YYYY-MM-DD` (JST 基準)。コレクション内の一意キー |
| slot | string | 必須 | `dinner` 固定 (Phase A)。将来 lunch / breakfast 追加用 |
| status | string | 必須 | `empty` / `proposed` / `confirmed` / `cooked` / `skipped` |
| proposal_id | string | 任意 | 紐づく `proposals` ドキュメント ID |
| dishes | array<map> | 任意 | 採用/提案時点の dishes スナップショット (`proposals.dishes` と同形)。各 dish は `{name, category, main_ingredient, reason, ingredients, steps}` |
| source | string | 必須 | `single` / `weekly_batch` / `manual` |
| notes | string | 任意 | メモ |
| created_at | timestamp | 必須 | |
| updated_at | timestamp | 必須 | |

### history / {historyId}

旧構造。Phase A 以降は `meal_plans.status=cooked` が真実のソース。`scripts/migrate_history_to_meal_plans.py` で backfill 後、互換読み取りのみ残す。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| family_id | string | 必須 | |
| recipe_id | string | 必須 | |
| cooked_at | timestamp | 必須 | 作った日時 |
| rating | integer | 任意 | 1〜5（0 は未評価） |
| notes | string | 任意 | メモ |

### proposals / {proposalId}

LLM 提案の全ログ。プロンプト改善の学習データ。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| family_id | string | 必須 | |
| requested_at | timestamp | 必須 | |
| prompt_version | string | 必須 | プロンプト YAML の version |
| model | string | 必須 | `claude-sonnet-4-6` など |
| input_tokens | integer | 必須 | |
| output_tokens | integer | 必須 | |
| cache_read_tokens | integer | 任意 | Prompt Caching 利用時 |
| latency_ms | integer | 必須 | |
| dishes | array<map> | 必須 | 提案献立。各 dish は `{name, category, main_ingredient, reason, ingredients, steps}`。週間提案は `days[].dishes[]` に同形で持つ |
| violations | array<map> | 任意 | ガード違反の内容 |
| retry_count | integer | 必須 | 0 または 1 |
| accepted | boolean | 任意 | ユーザーが採用したか |

### feedback / {feedbackId}

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| proposal_id | string | 必須 | |
| family_id | string | 必須 | |
| rating | string | 必須 | `good` / `neutral` / `pass` の 3 値（MVP） |
| comment | string | 任意 | 自由記述 |
| submitted_at | timestamp | 必須 | |

### llm_usage / {usageId}

LLM 呼出 1 件毎の利用ログ。コスト集計・予算チェック・キャッシュ効率分析に
使う。フラット top-level コレクションとし、`year_month` の denormalize で
クエリ用 index を不要にする。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| timestamp | timestamp | 必須 | UTC |
| family_id | string | 必須 | 呼出元家族 |
| purpose | string | 必須 | `single_day` / `weekly` / `other` |
| model | string | 必須 | `claude-sonnet-4-6` / `fake-sonnet` / `unknown` |
| year_month | string | 必須 | `YYYY-MM` denormalize（クエリ用） |
| input_tokens | integer | 必須 | 非キャッシュ入力 |
| output_tokens | integer | 必須 | |
| cache_read_tokens | integer | 必須 | Prompt Caching ヒット時 |
| cache_write_tokens | integer | 必須 | Prompt Caching 書込時 |
| latency_ms | integer | 必須 | 呼出レイテンシ |
| cost_jpy | float | 必須 | `PRICING_TABLE` + `USD_JPY_RATE` で算出 |
| success | boolean | 必須 | API 呼出成功か |
| retry_attempt | integer | 必須 | 0 / 1 |
| error_code | string | 任意 | 失敗時の例外クラス名等 |

Preflight でブロックされた呼出（API 未到達）は記録しない。BudgetGuardedClient が API 呼出 finally で append する。

### config / llm_budget

LLM 月次予算の運用パラメータ（単一ドキュメント）。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| monthly_jpy_limit | float | 必須 | 月次予算上限（JPY） |
| warn_threshold_pct | integer | 任意 | 警告閾値%（既定 80）|
| updated_at | timestamp | 任意 | 編集時刻 |

運用時は Firestore コンソールで直接編集する。アプリ起動時のシードでは
`merge=True` で書くため既存値は保護される。

## 3. Pydantic モデルとの対応

`src/recipe_system/domain/` に Pydantic モデルを配置し、Firestore ドキュメントとのシリアライズ/デシリアライズは `src/recipe_system/repository/` で行う。フィールド名は Firestore 側は snake_case、Python 側も snake_case で統一する。

## 4. Firestore セキュリティルール（雛形）

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    function isAllowedFamilyMember(familyId) {
      return request.auth != null
        && request.auth.token.email in
           get(/databases/$(database)/documents/families/$(familyId)).data.allowed_emails;
    }

    match /families/{familyId} {
      allow read, write: if isAllowedFamilyMember(familyId);

      match /members/{memberId} {
        allow read, write: if isAllowedFamilyMember(familyId);
      }
    }

    match /recipes/{recipeId} {
      allow read: if request.auth != null;
      allow write: if false;
    }

    match /meal_plans/{planId} {
      allow read, write: if request.auth != null
        && resource.data.family_id != null
        && isAllowedFamilyMember(resource.data.family_id);
    }

    match /shopping_lists/{listId} {
      allow read, write: if request.auth != null
        && resource.data.family_id != null
        && isAllowedFamilyMember(resource.data.family_id);
    }

    match /history/{historyId} {
      allow read, write: if request.auth != null
        && resource.data.family_id != null
        && isAllowedFamilyMember(resource.data.family_id);
    }

    match /proposals/{proposalId} {
      allow read: if request.auth != null
        && isAllowedFamilyMember(resource.data.family_id);
      allow write: if false;
    }

    match /feedback/{feedbackId} {
      allow read, write: if request.auth != null
        && isAllowedFamilyMember(resource.data.family_id);
    }
  }
}
```

`recipes` コレクションは全家族で共有する読み取り専用データ。書き込みは管理者がサーバサイドのサービスアカウント経由で行う。

## 5. インデックス設計

| コレクション | インデックス | 用途 |
|---|---|---|
| meal_plans | `family_id ASC, plan_date ASC` | カレンダー範囲取得 (週・月ビュー) |
| shopping_lists | `family_id ASC, week_start ASC` | 週次買い物リスト取得 |
| history | `family_id ASC, cooked_at DESC` | 旧履歴の取得 (互換用) |
| proposals | `family_id ASC, requested_at DESC` | 提案ログ一覧 |
| recipes | `category ASC, main_ingredient ASC` | 候補レシピ絞り込み |
| feedback | `proposal_id ASC, submitted_at DESC` | 提案ごとの評価取得 |

`firestore.indexes.json` にも同内容を記述する。

## 6. 初期データ投入

`data/seeds/` に以下を配置する。

- `family.example.yaml` - 家族プロファイルサンプル
- `recipes.example.yaml` - 定番レシピ 100 品サンプル
- `history.example.yaml` - 直近履歴サンプル

投入スクリプトは `scripts/seed_firestore.py`。`--emulator` フラグでローカルエミュレータに、`--project <id>` で本番にそれぞれ書き込む。本番家族プロファイル（`family.production.yaml` など）は git 管理から除外する。

## 7. バックアップ戦略

- Cloud Scheduler から週次で Firestore export ジョブを起動
- 出力先は Cloud Storage バケット（IAM を最小化）
- 保持期間は 12 週間（約 3 ヶ月）、バケットライフサイクルで自動削除
- 復元テストは年 1 回実施する

詳細手順は [operations.md](operations.md) に記述する。

## 変更履歴

### 2026-09: members に item_policies (通常は除去不要な食品の可/不可) を追加

`families/{id}/members/{id}` に `item_policies: map` を追加した。醤油・味噌・ごま油など、
そのアレルギーがあっても摂取できることが多い食品 (`allergens.yaml` の `usually_tolerated`) を
メンバーごとに可/不可で持つ。家族設定画面では選択必須、未設定の食品はガードレールで除去扱い
(ADR 0007)。値は `allow` / `block` のみで、それ以外 (大文字、map 以外など) が入ると家族の
読み込みが検証エラーになる (安全側。Firestore コンソールで直す)。書き込みは `merge=[フィールド名...]` でフィールドごと置き換える (`merge=True` は
入れ子の map を再帰マージし、外したアレルギーの設定が残るため)。backfill と未選択の一覧表示は
`scripts/migrate_add_item_policies.py` (dry-run 既定、本番手順は docstring)。

### 2026-09: dishes に steps (作り方) を追加

`meal_plans.dishes[]` / `proposals.dishes[]` / `proposals.days[].dishes[]` に
`steps: array<string>` (1 品 3-5 ステップ) を追加した。手順導入前のドキュメントは
steps を持たないが、読み出し側 (`MealPlanDish.steps`) は既定値 `()` で扱う。形を揃える
ための backfill は `scripts/migrate_add_dish_steps.py` (dry-run 既定、本番手順は docstring)。

# 009 ダッシュボード化と meal_plans 導入 (Phase A)

日付: 2026-05-24

## 背景

ユーザーから「TOP ページが求めている物と違う、ダッシュボード化してカレンダー風に週/月で
献立を管理したい。買い物リストや家族プロファイル編集も TOP から触れるようにしたい」との
方向転換要求。MVP の「1 食提案ボタン 1 つ」から大幅に拡張。

設計プランは `.claude/plans/top-image-3-agile-corbato.md` に Phase A〜E でまとめた。
本コミットは Phase A: データモデル基盤 + ダッシュボード骨格。

## 実装内容

### 新規 (Phase A)

1. **`domain/meal_plan.py`**: `MealPlan` / `MealPlanDish` / `MealPlanStatus` / `MealSlot` /
   `MealPlanSource` / `make_plan_id`。ドキュメント ID は `{family_id}_{YYYY-MM-DD}` で固定。

2. **`repository/meal_plan_repository.py`**: `get_meal_plan` / `list_meal_plans_in_range` /
   `upsert_meal_plan` / `update_status` / `delete_meal_plan` / `recent_cooked_recipe_names`。
   範囲取得は `plan_date` (`YYYY-MM-DD` 文字列) の比較で昇順ソート。

3. **`scripts/migrate_history_to_meal_plans.py`**: 既存 `history` と `proposals.accepted=True`
   を `meal_plans` に backfill。dry-run デフォルト、`--apply` で書き込み。idempotent。

4. **`services/calendar_view.py`**: `today_in_jst` / `monday_of` / `week_range` /
   `build_week_view` / `build_month_view`。週は月曜開始 (ISO 週)、月ビューは 6 週 42 セル固定。

5. **`routes/dashboard.py`**: GET `/` で今週ストリップ + 今日のカード。

6. **`routes/calendar.py`**: GET `/calendar/week?start=` と GET `/calendar/month?year=&month=`。
   月の前後計算は `_shift_month` ヘルパに分離。

7. **`routes/plans.py`**: GET `/plans/{date}` + POST {propose, confirm, cooked, skip, clear,
   feedback}。提案フロー (`_run_suggestion` バックグラウンド) は `meal_plans` の status を
   原子的に更新 (失敗時は `empty` に戻す)。

8. **テンプレート**: `index.html` 全面差し替え、`partials/calendar_cell.html`、
   `calendar/{week,month}.html`、`plans/{day,feedback_sent}.html`。`layout.html` は
   ダッシュボード向けに CSS を拡張 (week-grid / month-grid / day-cell / tabs / badge--*)。

### 変更

- `src/recipe_system/main.py`: 旧 `home.router` 削除、`dashboard.router` / `calendar_routes.router`
  / `plans.router` を登録。
- `src/recipe_system/web/routes/proposals.py`: POST `/proposals` を `/plans/{today}/propose`
  に 307 リダイレクト。POST `/proposals/{id}/accept` は対応する `meal_plan` を cooked に
  更新する分岐を追加。`_run_suggestion` は `plans.py` に移管したため削除。
- `src/recipe_system/domain/__init__.py`: meal_plan の export 追加。
- `docs/data-model.md`: `meal_plans` セクション追加、`history` を互換用と明記、インデックス
  リストに `meal_plans` を追加、セキュリティルール雛形に `meal_plans` 追加。
- `docs/ui-spec.md`: ユースケース一覧を 7 件に拡張、エンドポイント一覧をダッシュボード/
  カレンダー/日別予定/互換維持/認証共通に分類、画面遷移図を状態遷移ベースに更新、
  テンプレート構成図を更新。
- `docs/adr/0004-meal-plans-as-primary-record.md`: 新規 ADR (meal_plans を真実のソースとする
  決定とその理由)。

### 削除

- `src/recipe_system/web/routes/home.py`: dashboard.router に置き換え。

## 設計判断 (Phase A)

- **meal_plans を新コレクションとして分ける**: `proposals` を LLM ログとして純粋に保持し、
  `meal_plans` を予定の真実のソースに。proposals 拡張案より責務が明確。
- **週開始は月曜**: ISO 週に一致。`week_start` / `week_end` 計算も同。
- **plan_date は YYYY-MM-DD 文字列 (JST 基準)**: タイムゾーン混乱を永続化時点で排除。
  Firestore の範囲クエリも文字列比較で動く。
- **状態は 5 値**: `empty` / `proposed` / `confirmed` / `cooked` / `skipped`。
- **過去日への提案禁止**: POST `/plans/{date}/propose` は今日以降のみ受け付け。

## テスト結果

- ユニット + ガードレール: 48 passed (新規 `test_meal_plan` 3, `test_calendar_view` 9 含む)
- 統合 (エミュレータ): 7 passed (新規 `test_meal_plan_repository` 3 含む)
- ruff check / format: clean
- mypy: clean

## 既知の影響

- `tests/unit/test_config.py` 2 ケースの修正 (uncommitted な auth 改修で is_emulator 時に
  fake firebase config を返すようになったため、テストを `Settings(...)` 直接構築に変更)。
- `tests/unit/test_web_routes.py::test_認証済みならトップが表示される`: ダッシュボードが
  Firestore を読むようになったため monkeypatch で `get_firestore_client` /
  `list_meal_plans_in_range` を mock。

## 次フェーズの示唆

- Phase B (週間まとめ提案) は `services/weekly_planner.py` を新設し、既存
  `suggest_dinner` を 7 日分にスケールさせる。Prompt Caching は system + family_profile を
  単日・週間で共有させる構造に。
- Phase C (買い物リスト) は `meal_plans.dishes[].ingredients` を週内集約する。
  単位混在は MVP では別行扱い、統一は後回し。
- Phase D (プロファイル編集) は `repository/family_repository.py` に member CRUD を
  追加し、reviewed_at の自動更新を組み込む。

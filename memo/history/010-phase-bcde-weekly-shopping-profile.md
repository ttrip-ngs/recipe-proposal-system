# 010 Phase B-E: 週間提案・買い物リスト・プロファイル編集・仕上げ

日付: 2026-05-24

## 背景

Phase A (009) でダッシュボード骨格を完成させた後、ユーザーから「残りのタスクあれば進んで」
との指示。Phase B (週間提案) → C (買い物リスト) → D (プロファイル編集) → E (仕上げ) を
連続して実装した。設計プラン: `.claude/plans/top-image-3-agile-corbato.md`。

## Phase B: 週間まとめ提案

- 新プロンプト `suggest_weekly_dinner_{system,user}.yaml` (version: "1")
  - 7 日分の dishes を 1 回の LLM 呼び出しで生成
  - 連続 2 日で同じ main_ingredient を回避する制約を system に明示
- `domain/llm_output.py` に `LLMWeeklyProposal` / `LLMWeeklyDay` 追加
- `services/weekly_planner.py` 新設
  - `plan_week(context, llm)` で 7 日分の `DailyResult` を返す
  - block 違反があった日のみリトライ最大 1 回、リトライ後も残ればその日だけ `succeeded=False`
- `routes/plans.py` に `POST /plans/week/{week_start}/propose` 追加
  - 月曜日のみ受け付ける、過去週は 400
  - 既存 confirmed/cooked/skipped 日は上書きしない (overwritable = empty/proposed のみ)
  - source="weekly_batch" で `meal_plans` に書き込む
- `routes/calendar.py` の週ビューに `can_propose_week` フラグを追加し、テンプレートにボタン表示
- `llm/client.py` の `FakeVertexClient` に weekly プロンプト検出 → 週間 fake レスポンス追加
- `evaluation/runner.py` に `_evaluate_weekly_case` 振り分けロジック追加
- `evaluation/schema.py` を `scope`, `week_start`, weekly-specific expected fields に拡張
- `evaluation/golden/weekly_basic_cases.jsonl` (W001-W003)
- ドキュメント: `docs/llm-integration.md`, `docs/evaluation.md` 更新

ユニットテスト 5 件追加 (test_weekly_planner.py)。

## Phase C: 買い物リスト

- `domain/shopping_list.py` (`ShoppingList`, `ShoppingItem`, `iso_week_label`)
  - ドキュメント ID: `{family_id}_{ISO_week}` (例 `family-1_2026-W22`)
- `repository/shopping_list_repository.py` (get/upsert/toggle_item/add_item/remove_item/delete)
- `services/shopping_aggregator.py`
  - 週内の confirmed/cooked meal_plans から ingredients を集約
  - 同 canonical かつ同 unit は数量を合算、単位混在は別行
  - manual=True と既存 checked 状態は再生成時に保持
- `routes/shopping.py` (GET /shopping/week, regenerate, items CRUD)
- `templates/shopping/week.html` 新設
- `routes/dashboard.py` に shopping_list 取得を追加
- TOP ダッシュボードに「今週の買い物リスト」要約カード追加
- `layout.html` ナビに「買い物」追加
- ドキュメント: `docs/data-model.md` に shopping_lists コレクション・インデックス・セキュリティルール追記

ユニットテスト 11 件追加 (test_shopping_list.py 4, test_shopping_aggregator.py 7)。

## Phase D: 家族プロファイル編集

- `repository/family_repository.py` に `upsert_member` / `delete_member` /
  `update_family_meta` / `MemberNotFoundError` を追加
  - `upsert_member` は呼び出しのたびに `reviewed_at` を現在時刻にセット (家族プロファイル確認の証跡)
  - 内部で `_touch_family_updated_at` を呼んで family ドキュメントの updated_at も更新
- `routes/profile_edit.py` 新設
  - GET /profile/edit / POST /profile/members (追加) / POST /profile/members/{id} (更新) /
    POST /profile/members/{id}/delete / POST /profile/meta (家族名・allowed_emails 編集)
  - アレルゲンは `allergens.yaml` のグループ名チェックボックス、嫌い/好きは CSV 入力
- `templates/profile/edit.html` 新設 (HTMX なし、フォーム送信ベース)
- `templates/profile/show.html` に「編集」リンク追加
- ドキュメント: `docs/ui-spec.md` にプロファイル編集エンドポイント追記

統合テスト 3 件追加 (test_family_repository.py)。

## Phase E: 仕上げ

- `tests/integration/conftest.py` の `_require_emulator` フィクスチャに
  `get_settings.cache_clear()` と `get_firestore_client.cache_clear()` を追加。
  - 理由: 単体テストが先に `test-project` で Settings/Client をキャッシュした状態で
    統合テストが走ると、`recipe-system-dev` への切替が効かず `KeyError: 'name'` で
    family_repository tests が失敗した。lru_cache のクリアで解消。
- `docs/architecture.md` の全体図と Phase 境界表を Phase A-E 反映に更新
- `tmp/render_dashboard.py` をプロファイル・買い物リスト含めた全画面レンダリング対応
- `TASKS.md` の Phase B/C/D/E 完了反映

## 設計判断 (Phase B-E)

- **週間提案は 1 LLM 呼び出し**: 7 日分の dishes をまとめて `LLMWeeklyProposal` で受ける。
  Prompt Caching の system+family は単日提案と共通化してヒット率を上げる。
- **part-success 許容**: 週間提案で 1 日だけ block 違反が残ったらその日だけ `status=empty`
  で他の日は確定。週全体フェイルにはしない (利便性優先)。
- **shopping aggregator は単位変換しない**: 同 canonical + 同 unit のみ合算、混在は別行。
  単位変換テーブルは Phase 2 以降。
- **manual アイテムは regenerate で保持**: 集約再計算してもチェック済み・手動追加分は
  消えないよう preserve_items として merge。
- **member CRUD で reviewed_at 自動更新**: アレルゲン情報のレビュー証跡を自動化。
- **lru_cache クリアの位置**: テスト用に integration conftest 内で `get_settings`/
  `get_firestore_client` の cache_clear を実行。production コードには影響しない。

## テスト結果

全 80 テスト pass (unit 51 + guardrails 11 + integration 18)。
ruff / mypy / pytest すべて green。

## 未着手 (Phase 2 以降)

- 本番デプロイ (GCP プロジェクト確定 + Cloud Run + Vertex AI 権限)
- 実 Vertex AI でのゴールデンセット live 評価
- Cloud Build / GitHub Actions CI
- Haiku 分担・並列実行
- 月単位の栄養バランス可視化
- HTMX による週/月 in-place タブ切替 (現状はリンク遷移)
- shopping_aggregator の単位変換テーブル

## 既知の制約

- 未コミット状態の auth 改修 (dev ブランチで先行) は別 PR で扱う想定
- 月間ビューでのコマ表示は 7 列固定、モバイル向けの最適化は未実施
- 過去日には UI 上「未定」と表示されるが提案ボタンは非表示

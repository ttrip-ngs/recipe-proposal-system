# 011 週レビュー画面と一括確定 UI

日付: 2026-05-24

## 背景

ユーザー指摘: 1 週間まとめて提案 (`POST /plans/week/{week_start}/propose`) した後、
それを確定する UI が日別詳細 (`/plans/{date}`) にしかなく、7 日分を 1 日ずつ開いて
確定ボタンを押す必要があった。

3 つの改善案 (週ビューにまとめて確定ボタン / セル上にインライン操作 /
提案完了後のサマリーページ) のうち、サマリーページ案を採用。

## 変更点

### バックエンド: `src/recipe_system/web/routes/plans.py`

- 新規 `GET /plans/week/{week_start}/review`
  - 月曜のみ許容 (非月曜は 400)。過去週は閲覧可能 (UI 側で操作抑制)
  - 7 日分の `MealPlan` をまとめて取得し `build_week_view` で整形
  - 各日に紐づく `Proposal` を重複呼び出しを抑えてキャッシュ取得
  - pending 数 (`status=proposed` かつ `dishes` 空) と確定可能数を集計してテンプレに渡す
- 新規 `POST /plans/week/{week_start}/confirm-all`
  - 月曜のみ許容、過去週は 400
  - `status=proposed` かつ `dishes` 非空の日のみ `confirmed` に更新
  - pending 日や既に confirmed/cooked/skipped の日は触らない
  - 対象 0 件でもエラーにせず, レビュー画面へリダイレクトして UI でメッセージ表示
- 既存 `POST /plans/week/{week_start}/propose` のリダイレクト先を
  `/calendar/week?start=...` から `/plans/week/{week_start}/review` に変更
  (生成後すぐにレビュー画面に着地)

### テンプレ: `src/recipe_system/web/templates/plans/week_review.html`

- 7 日縦並びのレビュー画面 (モダン編集デザイン: editorial_hero / kicker /
  card / dish-card / action-bar)
- pending 日があるときは 3 秒ごとに自動リロード (既存 day.html と同じ
  `<meta http-equiv="refresh">` パターン)
- 状態別 UI:
  - pending: "考え中..." 表示, 一括確定ボタンは disabled
  - 確定可能 (proposed + dishes): 個別確定 / 別案提案 / 詳細リンク
  - 既確定/調理済/スキップ: 状態バッジと最小限のアクション
  - 空: 個別提案ボタン / スキップボタン
- ヘッダに一括アクション (まとめて確定 / 空き日に提案)
- 過去週は破壊的アクションを表示しない

### テンプレ: `src/recipe_system/web/templates/calendar/week.html`

- 「7 日分まとめて提案」ボタン横に「レビュー画面を開く」リンクを追加
- 過去週でも「この週をレビュー」リンクを表示 (閲覧用)

### テスト: `tests/unit/test_web_routes.py`

- 週レビューは月曜のみ受け付ける (非月曜は 400)
- 週レビューが空状態でも 200 を返す (Firestore モック)
- 週一括確定は月曜以外を拒否 (400)
- 週一括確定は過去週を拒否 (400)

## 設計判断

- **既存 day.html の確定ボタンは温存**。直接日付ページから入るフローも残す
- **対象 0 件の一括確定は 400 にしない**。HTTP エラーでなく UI で
  「確定対象の提案はありません」と表示する方が UX 上自然
- **過去週のレビュー閲覧は許可**。確定や別案依頼ボタンはテンプレ側で抑制
- **`build_week_view` を再利用**。weekday ラベル・is_today/is_past などの
  共通整形ロジックを services 層に閉じ込めたまま

## 動作確認

- `uv run ruff check src tests`: OK
- `uv run mypy src`: OK (49 files)
- `uv run pytest`: 84 passed

## 既知の制約・残課題

- `confirm-all` 経由で一括 confirmed 化した提案には個別の `accepted` フラグ
  (`mark_accepted`) を立てていない。現状の day.html `/plans/{date}/confirm`
  も同様なので非対称ではないが, 受容性メトリクスを追うなら別途検討
- レビュー画面に proposal の prompt_version / latency などのフッターは未表示
  (day.html にはある)。週単位だと提案 ID が混在するため意図的に省略

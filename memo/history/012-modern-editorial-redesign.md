# 012 - モダン編集デザイン (Modern Editorial Kitchen) 全面リファクタ

実施日: 2026-05-24
ブランチ: `feature/dashboard-meal-plans-20260524`

## 背景

Phase A-E（dashboard / meal_plans / 週間提案 / 買い物リスト / プロファイル編集）の機能実装は揃ったが、UI は `layout.html` インライン 500 行 CSS によるオレンジアクセント + システムフォントのフラットデザインで没個性。家族メンバーが毎日触れる体験としての上質さが不足していた。

加えて買い物中・調理中など片手操作のモバイル運用が中心であるにもかかわらず、以下のモバイル品質課題があった:

- 全 input が `padding: 6px` + font-size 未指定で iOS フォーカス時にズーム発生
- 全ボタンが 44×44 未満（WCAG タッチサイズ）
- `safe-area-inset`、`viewport-fit=cover`、`100dvh`、`theme-color` 一切未対応
- ヘッダーナビが 5 リンクで 375px 幅に収まらず横スクロール
- `shopping/week.html` のリスト行が flex 横並びで誤タップリスク

## 方針

「Modern Editorial Kitchen」アエスティック × モバイルファースト徹底の二軸:

- 和の余韻 + 編集デザイン（Shippori Mincho B1 × Noto Sans JP）
- ウォームクリーム（#FAF7F2）× 墨黒（#1A1A1A）× マスタード（#C58A3A）
- ライト/ダーク両対応（`prefers-color-scheme` 自動切替）
- bottom navigation, sticky action bar, scroll-snap week strip, 16px input, safe-area

詳細プランは `.claude/plans/frontend-design-inherited-tide.md` に記録。

## 実装サマリ（4 コミット）

### C1: 基盤 + layout/ダッシュボード (`79469f1`)

- 新設: `static/css/app.css`（650 行、@layer tokens/base/layout/components/utilities）
- 新設: `static/js/app.js`（reveal IntersectionObserver、当日中央スクロール、reduce-motion ガード）
- 新設: `partials/_macros.html`（status_badge / editorial_hero / bottom_nav / allergen_callout 等）
- 改修: `main.py`（`/static` を `StaticFiles` マウント）、`web/templating.py`（`STATIC_DIR` export）
- 改修: `layout.html`（インライン CSS 全削除、viewport-fit=cover, theme-color×2, apple-mobile-web-app-* 追加、bottom_nav 統合）
- 改修: `index.html`（編集マガジン風ヒーロー + 週ストリップ + Bento）
- 改修: `partials/calendar_cell.html`（新トークン対応、data-today で JS 連携）

### C2: 提案・カレンダー (`46d1f06`)

- 改修: `plans/day.html`（献立票風 dish-card、choice-grid フィードバック、sticky action bar、allergen_callout）
- 改修: `calendar/week.html`、`calendar/month.html`（モバイルは縦リストへ自動切替）
- 改修: `plans/feedback_sent.html`、`proposals/{feedback_sent,pending}.html`（editorial 階層統一）
- `proposals/detail.html` は別フィーチャー WIP のためスキップ（後述）

### C3: 買い物・プロファイル・履歴 (`7ae102d`)

- 改修: `shopping/week.html`（check-row 48×48 タップ領域、削除を `<details>` メニュー化、sticky summary-bar、inputmode/enterkeyhint）
- 改修: `profile/show.html`（member-card、アレルゲン/嫌い/好きを色分け chip）
- 改修: `profile/edit.html`（form-stack/form-row 1→2 カラム自動切替、大型 input）
- 改修: `history/list.html`（tnum 日付揃え + 罫線リスト）

### C4: 認証・エラー・ドキュメント・仕上げ（本コミット予定）

- 改修: `errors/{403,404,500}.html`（中央寄せヒーロー、kicker 階層）
- ドキュメント: `docs/ui-spec.md` に「9. デザインシステム」「10. モバイル設計」追加
- ドキュメント: `docs/development.md` に「11. 静的アセット運用」追加
- 本 memo (012) 追加、`TASKS.md` 反映
- `auth/login.html` + `static/js/login.js` 切り出しは別フィーチャー WIP が残っているため別 PR で扱う（後述）

## 検証

- 単体: `uv run pytest tests/unit/test_web_routes.py` 7 件 pass（C1〜C4 各時点）
- 視覚: `tmp/dev_preview.py`（auth/Firestore モック化したプレビューサーバ）+ `playwright-cli` で
  - デスクトップ (1280×900) × ライト/ダーク
  - モバイル (390×844) × ライト/ダーク
  - 主要画面 8 つ（dashboard / calendar/week / calendar/month / plans/day / shopping/week / profile / profile/edit / history）
  - 結果は `tmp/screenshots/011-redesign/` に保存
- 既存文字列アサーション（今週/献立を提案/見つかりません/ログイン/sign-in-google）はすべて維持

## 未対応 / 引き継ぎ事項

### auth 関連 WIP との衝突

別フィーチャーで進行中の auth 改修（`docker-compose.yml`, `config.py`, `web/routes/auth.py`, `web/templates/auth/login.html`, `web/templates/proposals/detail.html`, `firebase.docker.json`）が未コミットで残っている。これらに含まれる以下 2 テンプレートは本リファクタの対象だが、別フィーチャーとの混合を避けるため触っていない:

- `src/recipe_system/web/templates/auth/login.html` - Modern Editorial 化と `static/js/login.js` 切り出しを別 PR で実施
- `src/recipe_system/web/templates/proposals/detail.html` - 同様に editorial 階層適用を別 PR で

これらは既存の旧スタイルクラス（`.card`, `.btn`, `.btn-outline` 等）を使っているため、新 CSS（`.card`, `.btn`, `.btn--outline`）と表記揺れが起きる。`.btn-outline` も既存セレクタとして残しても良いが、命名衝突を避けるため、auth 改修の PR が main にマージされた直後に追従 PR を出すのが望ましい。

### Cloud Run / CDN

`/static` は FastAPI 直配信。将来的にハッシュ付きパス + Cloud CDN への移行を検討（ui-spec.md 11.2 参照）。

### PWA 化への布石

`apple-mobile-web-app-capable` / `theme-color` メタは入れたが、`manifest.json` / Service Worker は未対応。買い物リストのオフライン閲覧は次フェーズ候補。

### tmp/dev_preview.py

`.gitignore` 配下なので git 管理外。将来 UI 改修時の視覚検証で再利用可能（docs/development.md 11.4 参照）。

## 触ったファイル一覧

新設:
- `src/recipe_system/web/static/css/app.css`
- `src/recipe_system/web/static/js/app.js`
- `src/recipe_system/web/templates/partials/_macros.html`
- `memo/history/012-modern-editorial-redesign.md`（本ファイル）

改修:
- `src/recipe_system/main.py`
- `src/recipe_system/web/templating.py`
- `src/recipe_system/web/templates/layout.html`
- `src/recipe_system/web/templates/index.html`
- `src/recipe_system/web/templates/partials/calendar_cell.html`
- `src/recipe_system/web/templates/plans/day.html`
- `src/recipe_system/web/templates/plans/feedback_sent.html`
- `src/recipe_system/web/templates/calendar/week.html`
- `src/recipe_system/web/templates/calendar/month.html`
- `src/recipe_system/web/templates/proposals/feedback_sent.html`
- `src/recipe_system/web/templates/proposals/pending.html`
- `src/recipe_system/web/templates/shopping/week.html`
- `src/recipe_system/web/templates/profile/show.html`
- `src/recipe_system/web/templates/profile/edit.html`
- `src/recipe_system/web/templates/history/list.html`
- `src/recipe_system/web/templates/errors/403.html`
- `src/recipe_system/web/templates/errors/404.html`
- `src/recipe_system/web/templates/errors/500.html`
- `docs/ui-spec.md`
- `docs/development.md`
- `TASKS.md`

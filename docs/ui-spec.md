# UI 仕様

## 1. ユースケース一覧

Phase A 完了時点で提供するユースケース (TOP ダッシュボード化に伴い拡張)。

| ID | ユースケース | 主アクター |
|---|---|---|
| UC-01 | 今週のカレンダーで献立予定を俯瞰する | 家族メンバー |
| UC-02 | 月間カレンダーで献立予定を俯瞰する | 家族メンバー |
| UC-03 | 指定した日の夕食献立提案を受ける | 家族メンバー |
| UC-04 | 提案を「確定」「調理済」「スキップ」「クリア」する | 家族メンバー |
| UC-05 | 提案へ直後フィードバック（3 択）を送る | 家族メンバー |
| UC-06 | 過去の調理済献立を履歴一覧で閲覧する | 家族メンバー |
| UC-07 | 家族プロファイル（アレルゲン・嫌い）を閲覧する | 家族メンバー |

Phase B 以降: 週間まとめ提案 (UC-08)、買い物リスト (UC-09)、プロファイル編集 (UC-10)、レシピ追加 (UC-11)。詳細は `.claude/plans/top-image-3-agile-corbato.md` の Phase 計画を参照。

## 2. エンドポイント一覧

FastAPI ルーターを `src/recipe_system/web/routes/` 配下に配置する。

### ダッシュボード・カレンダー

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| GET | `/` | 要 | ダッシュボード (今週ストリップ + 今日のカード) |
| GET | `/calendar/week?start=YYYY-MM-DD` | 要 | 週ビュー (月曜開始) |
| GET | `/calendar/month?year=Y&month=M` | 要 | 月ビュー (6 週固定 42 セル) |

### 日別予定の操作 (`meal_plans`)

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| GET | `/plans/{date}` | 要 | 日別詳細 (献立・状態・操作) |
| POST | `/plans/{date}/propose` | 要 | 単日提案リクエスト (非同期開始, 今日以降のみ) |
| POST | `/plans/{date}/confirm` | 要 | 提案を確定 (status: proposed → confirmed) |
| POST | `/plans/{date}/cooked` | 要 | 調理済記録 (status → cooked) |
| POST | `/plans/{date}/skip` | 要 | スキップ (status → skipped) |
| POST | `/plans/{date}/clear` | 要 | 予定をクリア (ドキュメント削除) |
| POST | `/plans/{date}/feedback` | 要 | 提案へのフィードバック送信 |

### 買い物リスト (Phase C)

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| GET | `/shopping/week?start=YYYY-MM-DD` | 要 | 週次買い物リスト表示 |
| POST | `/shopping/week/{week_start}/regenerate` | 要 | confirmed/cooked 献立から再集約 |
| POST | `/shopping/week/{week_start}/items` | 要 | 手動アイテム追加 |
| POST | `/shopping/week/{week_start}/items/{item_id}/toggle` | 要 | 購入チェック切替 |
| POST | `/shopping/week/{week_start}/items/{item_id}/delete` | 要 | アイテム削除 |

### 互換維持

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| POST | `/proposals` | 要 | 旧 URL → `/plans/{today}/propose` に 307 リダイレクト |
| GET | `/proposals/{proposal_id}` | 要 | 過去提案の閲覧 (履歴等からの直リンク用) |
| POST | `/proposals/{proposal_id}/accept` | 要 | 採用 + 対応する `meal_plan` を cooked に更新 |
| POST | `/proposals/{proposal_id}/feedback` | 要 | 旧フィードバックエンドポイント |

### 家族プロファイル編集 (Phase D)

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| GET | `/profile/edit` | 要 | プロファイル編集フォーム |
| POST | `/profile/members` | 要 | メンバー追加 |
| POST | `/profile/members/{member_id}` | 要 | メンバー更新 |
| POST | `/profile/members/{member_id}/delete` | 要 | メンバー削除 |
| POST | `/profile/meta` | 要 | 家族名・allowed_emails 更新 |

### 認証・共通

| メソッド | パス | 認証 | 処理 |
|---|---|---|---|
| GET | `/history` | 要 | 履歴一覧 (旧 history コレクション、互換用) |
| GET | `/profile` | 要 | 家族プロファイル表示 |
| GET | `/login` | 不要 | Firebase Auth サインイン画面 |
| POST | `/session` | 不要 | ID トークン受領・セッション確立 |
| POST | `/logout` | 要 | セッション破棄 |
| GET | `/healthz` | 不要 | ヘルスチェック |

レスポンスは HTML（Jinja2 テンプレート）。API 単体呼び出しは MVP では提供しない。週は月曜始まり (ISO 週) で固定、日付はすべて JST 基準。

## 3. 画面遷移

```
[/login]
   |
   | Firebase Auth 成功
   v
[/] ダッシュボード (今週ストリップ + 今日のカード)
   |       |
   |       +---> [/calendar/week] 週ビュー
   |       |
   |       +---> [/calendar/month] 月ビュー
   |       |
   |       +---> [/history] 旧履歴 (互換)
   |       |
   |       +---> [/profile] 家族プロファイル
   v
[/plans/{date}] 日別詳細
   |
   |--- 状態 empty -----+--> [献立を提案] POST /plans/{date}/propose
   |                    +--> [スキップ]    POST /plans/{date}/skip
   |
   |--- 状態 proposed --+--> [この献立で確定] POST /plans/{date}/confirm
   |                    +--> [別案を提案]    POST /plans/{date}/propose
   |                    +--> [調理済]        POST /plans/{date}/cooked
   |                    +--> [フィードバック] POST /plans/{date}/feedback
   |                    +--> [クリア]        POST /plans/{date}/clear
   |
   |--- 状態 confirmed -+--> [調理済]        POST /plans/{date}/cooked
   |                    +--> [クリア]        POST /plans/{date}/clear
   |
   |--- 状態 cooked ----+--> (読み取り専用 + フィードバックのみ)
   |
   +--- 状態 skipped ---+--> [スキップ解除]  POST /plans/{date}/clear

提案中の polling:
[/plans/{date}] 状態 proposed かつ proposals.status=pending の間,
                <meta refresh content="3"> で 3 秒ごと自動再読込.
                LLM 応答到着で dishes が埋まり, 同じ URL で結果表示に切り替わる.
```

## 4. Jinja2 テンプレート構成

```
src/recipe_system/web/templates/
  ├── layout.html              # 共通レイアウト (ヘッダ・ナビ・CSS)
  ├── index.html               # ダッシュボード (TOP)
  ├── partials/
  │   ├── calendar_cell.html   # 1 日セル (週・月ビュー共通)
  │   └── flash.html           # フラッシュメッセージ (将来)
  ├── calendar/
  │   ├── week.html            # 週ビュー (7 日グリッド)
  │   └── month.html           # 月ビュー (6 週固定 42 セル)
  ├── plans/
  │   ├── day.html             # 日別詳細 (献立・状態・操作)
  │   └── feedback_sent.html   # フィードバック送信完了
  ├── shopping/
  │   └── week.html            # 週次買い物リスト (Phase C)
  ├── profile/
  │   ├── show.html            # プロファイル閲覧
  │   └── edit.html            # プロファイル編集 (Phase D)
  ├── proposals/               # 互換用 (旧 URL 直リンク)
  │   ├── pending.html
  │   ├── detail.html
  │   └── feedback_sent.html
  ├── history/
  │   └── list.html
  ├── profile/
  │   └── show.html
  ├── auth/
  │   └── login.html
  └── errors/
      ├── 403.html
      ├── 404.html
      └── 500.html
```

## 5. フィードバック UX

### 5.1 収集タイミング

MVP では提案直後の 1 回のみ収集する。事後（調理後）フィードバックは Phase 2 で追加する。

### 5.2 フォーマット

3 択 + 自由記述。

- `good` - 良い提案だった
- `neutral` - 普通
- `pass` - 今度はパス

```
<form method="post" action="/proposals/{id}/feedback">
  <fieldset>
    <legend>この提案はいかがでしたか？</legend>
    <label><input type="radio" name="rating" value="good"> 良い</label>
    <label><input type="radio" name="rating" value="neutral"> 普通</label>
    <label><input type="radio" name="rating" value="pass"> パス</label>
  </fieldset>
  <textarea name="comment" rows="3" placeholder="コメント（任意）"></textarea>
  <button type="submit">送信</button>
</form>
```

### 5.3 格納

`feedback/{feedbackId}` コレクション（[data-model.md](data-model.md) §2）に格納する。

## 6. 非同期表示（Cold Start 対策）

### 6.1 方式

HTMX ベースの polling を採用する。提案リクエスト直後に `/proposals/{id}` へリダイレクトし、該当ページがまだ `pending` 状態なら 3 秒間隔で自動再読込する。

```html
<div hx-get="/proposals/{{ proposal_id }}"
     hx-trigger="load delay:3s"
     hx-swap="outerHTML">
  <p>考え中... ({{ elapsed_seconds }}s)</p>
</div>
```

### 6.2 タイムアウト

30 秒経過しても `ready` にならない場合はエラー画面に遷移する。Cloud Run + Vertex AI のレイテンシは通常 10 秒以内を想定するが、マージンを取る。

### 6.3 実装方針

提案処理は FastAPI の `BackgroundTasks` で非同期実行する。MVP では在宅・少人数利用のため Celery 等の本格的なワーカーは導入しない。

## 7. エラー表示

### 7.1 ガードレール全ブロック

リトライ後もガード違反が残る場合、以下を表示する。

```
安全な提案を作成できませんでした。

家族のアレルギー・嫌いな食材を考慮した結果、
条件を満たす献立を生成できませんでした。
お手数ですが、候補レシピから手動でお選びください。

[候補レシピから選ぶ]  [再度提案を依頼]
```

フェイルオープンしない。家庭内事故の温床を作らない。

### 7.2 LLM タイムアウト・障害

```
LLM への接続に失敗しました。
しばらく時間を置いて再度お試しください。
```

### 7.3 認証エラー

```
このアカウントは家族メンバーとして登録されていません。
管理者にご連絡ください。
```

## 8. 国際化・アクセシビリティ

- 日本語 UTF-8 固定（MVP）
- フォームラベルと input の関連付け（`for` / `id`）
- 色だけに依存した情報伝達を避ける（アレルゲン警告は文字 + アイコン）
- Firebase Auth のデフォルト UI は日本語モードで統一

## 9. デザインシステム（Modern Editorial Kitchen）

2026-05-24 に「家庭の献立帖」アエスティックへ全画面リファクタした。設計思想は「和の余韻 × 編集デザイン」。明朝ディスプレイ + ゴシック本文、ウォームクリーム + 墨黒 + マスタード季節アクセントで毎日触れても疲れない上質さを目指す。

ライト/ダークは OS 設定に追従するほか、ヘッダー右の **テーマトグルボタン**で「システム追従 → ライト固定 → ダーク固定」を循環切替できる。選択は `localStorage` に保存し、paint 前のインラインスクリプトで適用してフラッシュを防ぐ。

### 9.1 ディレクトリ構成

- `src/recipe_system/web/static/css/app.css` - 単一エントリ（`@layer tokens, base, layout, components, utilities;`）
- `src/recipe_system/web/static/js/app.js` - 進歩的強化（reveal / 当日スクロール / チェック楽観反映）
- `src/recipe_system/web/templates/partials/_macros.html` - 再利用マクロ
  - `editorial_hero(headline_text, date_text=None, meta_html=None, kicker_text=None, lede_text=None)` - 情報密度重視のページヘッダ (タイトル + 日付 + 右寄せメタを 1〜2 行に圧縮。`kicker_text` / `lede_text` は後方互換のため受けるが現状はレンダリングしない)
  - `status_badge(status)` - 状態バッジ（色 + dot 形状の二重符号化）
  - `kicker(text)` / `headline(text, level)` / `lede(text)`
  - `chip(label, tone)` / `allergen_callout(violations)` / `bottom_nav(active)`
- `main.py` で `/static` を `StaticFiles` マウント（`templating.py` の `STATIC_DIR` を経由）

### 9.2 デザイントークン

CSS変数で集約。ライト/ダークは `prefers-color-scheme` で自動切替。

- 色: `--bg` / `--bg-elevated` / `--bg-sunken` / `--ink` / `--ink-muted` / `--ink-subtle` / `--rule` / `--accent`（マスタード #C58A3A 系）/ `--accent-soft` / `--danger`
- 状態色 5 種（empty/proposed/confirmed/cooked/skipped）に `*-bg / -ink / -dot` を light/dark 個別定義
- フォント: `--font-display`（Shippori Mincho B1、fallback Noto Serif JP）+ `--font-body`（Noto Sans JP）、`tabular-nums` を強制
- 間隔: `--space-1..16`（4px 基底）、角丸: `--radius-1/2/3/pill`、影: `--shadow-paper / -lift / -bar`
- モーション: `--motion-fast/base/slow` + `--ease-emphasized`、`@media (prefers-reduced-motion)` で無効化

### 9.3 状態の二重・三重符号化

- バッジは色 + dot 形状で二重符号: `●`（confirmed）/ `◇`（proposed）/ `✓`（cooked）/ `/`（skipped）/ `—`（empty）。色覚多様性に配慮
- アレルゲン警告（`allergen_callout` マクロ）は三重符号: アイコン + 文字 + 色

## 10. モバイル設計

家族メンバーは買い物中・調理中など片手操作で利用する場面が多く、モバイル品質を優先設計。

### 10.1 ナビゲーション

- モバイル（< 768px）: ヘッダーは「ロゴ + ログアウト」のみに縮約。**bottom navigation**（5 タブ: 今日 / 週 / 買い物 / 家族 / 記録）が `position: fixed; bottom: 0; padding-bottom: env(safe-area-inset-bottom)` で常時表示
- デスクトップ（≥ 768px）: 水平ヘッダーナビ、bottom nav は `display: none`

### 10.2 タッチターゲット・親指リーチ

- すべての `button`, `a.btn`, `[role="button"]` に `min-height: var(--touch-min)` (44px)
- `@media (pointer: coarse)` でさらに `min-height: var(--touch-comfortable)` (48px)
- `plans/day.html` の主要アクション（確定 / 別案 / クリア）は `.action-bar--sticky` でモバイル時に画面下に固定
- 買い物リストの削除ボタンは `<details>` 内メニューに退避して誤タップ事故防止
- 料理カードの材料と作り方は `partials/_macros.html` の `dish_details` で共通化し、
  `<details>`「材料を見る」「作り方を見る」で折りたたむ (日別詳細 / 週レビュー / 提案結果で共通)。
  作り方は番号付きリスト (`ol.dish-steps`)。手順導入 (2026-09) 前の献立は作り方を表示しない

### 10.3 フォーム入力

- 全 input/textarea で `font-size: 16px` 強制（iOS フォーカス時ズーム抑止）、`padding: 12px 14px`
- `inputmode="decimal"`（数量）、`autocomplete="off"`（食材名 / 単位）、`enterkeyhint="next"|"done"` を適切に設定
- ラジオ・チェックボックスは `:has(:checked)` でラベルカード全体を強調（`.choice-grid`）

### 10.4 viewport / safe-area / theme-color

- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`
- `body` に `min-height: 100vh; min-height: 100dvh;` の二段フォールバック
- ヘッダー: `padding-top: max(12px, env(safe-area-inset-top))`、bottom nav: `padding-bottom: max(6px + env(safe-area-inset-bottom), ...)`
- `<meta name="theme-color">` をライト/ダーク両モード指定（iOS Safari / Android Chrome のブラウザ UI を背景と一体化）
- `<meta name="apple-mobile-web-app-capable" content="yes">`（将来 PWA 化への布石）

### 10.5 週ストリップ

- モバイル: `scroll-snap-type: x mandatory` で 7 日カードを横スクロール、各カード 72vw 幅、当日カードを `app.js` で初期中央スクロール
- 下部に dot indicator（曜日マーカー）、当日マーカーを強調
- デスクトップ: 7 列 `grid` を維持

### 10.6 買い物リストの一画面 UX

- リスト行（`.check-row`）は 48×48 のチェックボックスタップ領域、`<label>` 相当の全体タップ
- `.summary-bar` を sticky 配置（残り N / 全 M を常時可視）。N / M は「買うもの」のみを数える
- 調味料などの常備品は「常備品・調味料（在庫を確認）」欄に分け、`<details>` で既定折りたたみ。常備品の判定は `services/shopping_dictionary.yaml` で行い、手動追加したアイテムは常に「買うもの」に入る
- 同じ食材は単位が違っても 1 行にまとめ、数量を「8g + 1片」のように並記する
- `@media print` 対応で罫線 + 黒文字の最小スタイル（紙の買い物リストとして持ち出し可能）

### 10.7 z-index 階層

- `--z-base: 1` / `--z-sticky: 40` / `--z-action: 50` / `--z-nav: 60` / `--z-modal: 80`
- sticky 集計バー → sticky action bar → bottom nav の順で重ねる

## 11. 関連ドキュメント

- [architecture.md](architecture.md) - 全体フロー
- [operations.md](operations.md) - cold start 対策と実運用
- [data-model.md](data-model.md) - proposal / feedback スキーマ
- [development.md](development.md) - 静的アセット運用（11 章）

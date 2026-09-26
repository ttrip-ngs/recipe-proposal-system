# 015 ヒーロー圧縮と中2病的キャッチコピー除去

2026-05-25

## 背景

ダッシュボード・週ビュー・買い物・履歴など各画面の冒頭に巨大な装飾ヒーロー (kicker + 大きな headline + 装飾線 + lede) が置かれており、`家族の献立、ひとめで。` `一週間を見渡す。` `食卓のあしあと。` 等のキャッチコピーが視認情報密度を下げ、本文 (bento / cards) を画面下に押しやっていた。

## 変更概要

- `editorial_hero` マクロを再設計し、`headline_text + date_text + meta_html` を 1〜2 行に圧縮。装飾線・lede のレンダリングは廃止。後方互換のため `kicker_text` / `lede_text` 引数は受けるが無視する。
- 各テンプレートのヒーローを、データ駆動の実用文に置換:
  - index: `今週の献立` + 日付 + `確定 N / 提案 N / 未定 N`
  - calendar/week: `週ビュー` + 週ラベル + 同上カウント
  - calendar/month: `月ビュー` + 月ラベル + 月内日数カウント
  - plans/week_review: `週レビュー` + 週ラベル + `確定可 N / 生成中 N`
  - shopping/week: `買い物リスト` + 週ラベル + `残り N / 全 N 品`
  - admin/usage: `LLM 利用状況` + 年月 + `¥X / ¥Y (Z%) · 呼出 N 件`
  - profile/show: 家族名 + 更新日 + `メンバー N 名 · アレルゲン登録 N 件`
  - profile/edit: `(家族名) — 編集` + メンバー数/許可メール数
  - history: `調理済の記録` + 件数
- plans/day.html: 手書きの editorial hero を `M/D (曜) — 主菜名` 形式に圧縮し、状態 + 品数を右寄せメタへ。
- 各 card__head の英字 kicker (`Today` / `Shopping` / `Quick links` / `Menu` / `Empty` / `Skipped` / `Recent` / `Batch` / `Summary` / `Thanks` / `Add` / `Family` / `Members` / `AI Budget` 等) を、その card の h2/h3 タイトルか動的な card__meta に置換。
- layout.html フッターから `家族で囲む食卓のための小さな帖面` を削除。
- profile/show.html の `Allergen` / `Dislike` / `Like` kicker を `アレルゲン (N)` / `嫌い (N)` / `好き (N)` に。
- plans/day.html の Feedback fieldset legend を日本語化。

## CSS 調整 (src/recipe_system/web/static/css/app.css)

`.editorial` 系を flex / inline 中心の密度重視レイアウトに変更:

- `.editorial`: grid → flex (baseline 整列、wrap)。上下 margin を `space-6 / space-10` → `space-4 / space-5` に縮小。
- `.editorial__headline`: `clamp(1.9rem, 5vw + 0.5rem, 3rem)` → `clamp(1.25rem, 1.4vw + 0.9rem, 1.65rem)`。leading も tight → snug。
- `.editorial__rule`: 装飾線は `display: none` で撤去。
- `.editorial__meta`: 右寄せメタ用クラスを新設 (`margin-left: auto`, `font-variant-numeric: tabular-nums`)。
- `.editorial__lede`: 残置するが flex-basis 100% でフルブリードに。

## 検証

- 全 22 テンプレートで Jinja2 構文 OK。
- `uv run pytest` 116/116 通過。`tests/unit/test_web_routes.py` の `assert "Weekly Review" in r.text or "週レビュー" in r.text` は新ヘッダ `週レビュー` でパス。
- TestClient で `/`, `/calendar/week`, `/calendar/month`, `/plans/week/.../review` を直接レンダリングし、禁止コピー (`ひとめで`, `見渡す`, `あしあと`, `一望`, `食卓のための小さな帖面`, `やわらかく提案`, `家族で囲む食卓`) の混入なしを確認。
- `curl /login` でフッター刷新を確認。

## 後の開発に必要な注意

- `editorial_hero` の `kicker_text` / `lede_text` 引数は後方互換のため受けるだけで描画されない。新規ページではこれらを渡さず、`headline_text` `date_text` `meta_html` のみ使うこと。
- `meta_html` は `|safe` で展開するため、ユーザー入力を含めず固定テンプレ + Python `%` フォーマットで生成する運用。
- 旧ヒーローのフォントサイズに依存していたページがあれば、`text-hero` トークン (3rem) はもう editorial では使われない点に注意。
- profile/show.html のアレルゲン総数集計は frozenset を `sum` できないため `namespace` ループで集計している ([[hero-density-cleanup]] 参照)。

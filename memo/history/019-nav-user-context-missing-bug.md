# 019 history/proposalsルートでナビが未ログイン表示になる不具合修正

## 背景

apple-container (Apple 製の軽量コンテナランタイム) 経由で Firestore/Auth
エミュレータを起動し、ブラウザで実際にアプリを操作して動作確認していたところ、
ユーザーから「記録ページに飛ぶとメニューがログインになって勝手にログアウト
される」との報告があった。

再現したところ、`/history` ページはナビゲーションが「ログイン」リンク表示に
なる (通常は「ログアウト」ボタン) ものの、ページ本体のデータ (調理済の記録)
は正常に取得できており、実際にはログアウトされていなかった。

## 原因

`layout.html` のナビゲーションは Jinja2 テンプレート変数 `user` の有無で
「ログアウト」ボタンと「ログイン」リンクを出し分ける (`{% if user %}`)。
`web/routes/history.py` の `show_history` は `Depends(current_user)` で
認証済みユーザーを取得しているにもかかわらず、`TemplateResponse` の
コンテキストに `"user": user` を含めていなかったため、テンプレート側では
`user` が未定義 (Undefined → falsy) となり、ナビが未ログイン状態のレンダリング
になっていた。

全ルートファイルを確認したところ、同様の漏れが `web/routes/proposals.py` の
3 箇所 (`GET /proposals/{id}` の pending 分岐・ready 分岐、
`POST /proposals/{id}/feedback`) にもあった。他のルート
(dashboard/calendar/plans/shopping/profile/profile_edit/admin) は正しく
`user` を渡していた。

## 実施した変更

- `web/routes/history.py`: `TemplateResponse` に `"user": user` を追加
- `web/routes/proposals.py`: 3 箇所すべてに `"user": user` を追加
- `tests/integration/test_dashboard_e2e.py` に回帰テストを追加:
  - `/history` が認証済みで「ログアウト」を表示し「ログイン」リンクを
    含まないこと
  - `/proposals/{id}` (pending / ready の両状態) と
    `/proposals/{id}/feedback` も同様

## 検証

- `uv run pytest` 149 件通過 (新規 2 件含む、統合テストは Firestore
  エミュレータ接続下で実行)
- `ruff check` / `ruff format` / `mypy` 全通過
- ブラウザで実際に `/history` に遷移し、ナビが「今日 / カレンダー / 買い物 /
  家族 / 記録」+「ログアウト」と正しく表示されることを目視確認

## 後の開発に必要な注意

- 新しいページ・ルートを追加する際は `TemplateResponse` のコンテキストに
  `"user": user` を含め忘れないこと。`layout.html` のナビ描画がこれに依存する
- 今回のバグは apple-container 経由でのローカル実機検証で見つかった。
  Fake LLM + モック中心の単体テストだけでは検出できない類の不具合であり、
  実ブラウザでの手動確認の価値を裏付ける事例

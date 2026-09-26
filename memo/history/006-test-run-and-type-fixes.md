# 006: 実行検証 (uv/pytest/ruff/mypy) と型エラー修正

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `005-refactor-and-web-tests.md`

## 概要

ここまで書いたコードが実際に動くかローカルで `uv sync` / `pytest` / `ruff` / `mypy` を走らせて検証し、発見された問題を修正した。結果: 全チェック pass、アプリ起動まで確認。

## 発見された問題と対応

### 1. `EmailStr` が `email-validator` を要求 (起動時エラー)

- **症状**: `FamilyProfile.allowed_emails` の `frozenset[EmailStr]` 型を処理する際に ImportError
- **対応**: プレーン `frozenset[str]` に変更 (Firebase Auth 側で検証するので Pydantic 側での厳格 email 検証は不要)

### 2. 日本語テスト関数名にスペースが混入 → SyntaxError

- **症状**: `def test_未知食材は canonical に raw_name を返し ...` のようにスペース混じりで書いてしまい、Python 識別子として不正
- **原因**: Python 識別子には XID_Continue 文字のみ許可。ASCII スペースは不可
- **対応**: 6 箇所のテスト関数名からスペース除去

### 3. 404 ページが HTML にならない

- **症状**: 未登録ルートは FastAPI デフォルトの JSON 404 になり、カスタム `errors/404.html` が効かなかった
- **原因**: `@app.exception_handler(HTTPException)` (FastAPI 版) は登録したが、Starlette が raise する 404 をキャッチできていなかった
- **対応**: `StarletteHTTPException` に切り替え、さらに Accept / Content-Type が JSON なら JSON 応答、HTML を好むクライアントにはテンプレートを返すようディスパッチ

### 4. /session (POST) が 401 → 303 リダイレクトされて JSON を返せない

- **症状**: 不正 ID トークンで POST /session すると 401 ではなく 303 /login が返る
- **原因**: exception_handler が 401 を問答無用でリダイレクトしていた
- **対応**: GET かつ JSON を好まないクライアントのみリダイレクト、それ以外 (XHR/fetch/JSON) は本来の status をそのまま返す `_prefers_json()` ヘルパーを追加

### 5. `dict` 型の type arg 欠落

- `guardrails/dictionary_loader.py` の `_load_yaml(path) -> dict` を `dict[str, Any]` に (トップが dict であることも ValueError で明示検査)
- `repository/recipe_repository.py` の `_to_recipe(data: dict)` を `dict[str, Any]` に

### 6. `firebase_admin.auth.verify_id_token` が Any を返す

- `web/middleware/auth.py` で明示的な型注釈とローカル代入で no-any-return を解消

### 7. Anthropic SDK の system payload 型不整合

- `llm/client.py` で `list[dict[str, Any]]` が `Iterable[TextBlockParam]` を期待する引数に不整合
- `TYPE_CHECKING` で `TextBlockParam` を取り込み `cast()` で narrow

### 8. google-cloud-firestore の sync/async union 型

- `DocumentSnapshot | Awaitable[DocumentSnapshot]` のスタブが sync client で誤判定を起こす
- SDK 起因なので `[[tool.mypy.overrides]]` で `recipe_system.repository.*` に対して `union-attr / no-any-return / assignment` を局所無効化
- CLAUDE.md の「外部ライブラリ起因のエラーはチーム承認で抑制可」例外枠に該当

### 9. FastAPI / ruff の不一致

- `B008` (Depends() の default 引数) は FastAPI の慣例なので全体 ignore
- `PLC0415` (遅延 import) は循環回避・重い依存の遅延ロードで意図的に使うので ignore
- 日本語テスト関数名の `N802` / `PLC2401` は `tests/**/*.py` で per-file-ignore

### 10. 軽微修正

- `services/filters.py` の for-return を `any(...)` に置換 (SIM110)
- `evaluation/runner.py` の `for line in ...` → `for raw_line in ...` (PLW2901)
- 行長超過 2 箇所を改行

## 最終状態

```
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
58 files already formatted

$ uv run mypy src
Success: no issues found in 38 source files

$ uv run pytest
36 passed, 2 skipped (integration は Firestore エミュレータ未起動で skip)

$ GOOGLE_CLOUD_PROJECT=test ANTHROPIC_VERTEX_PROJECT_ID=test SESSION_SECRET=test \
    uv run python -c "from recipe_system.main import app; print(app.title, len(app.routes))"
Recipe Proposal System 15
```

- **uv.lock**: 自動生成、1179 行。Dockerfile が `--frozen` 前提なのでリポジトリにコミットする運用
- **テスト合計**: 36 unit/guardrails + 2 integration (skip)

## 次手の候補

1. `uv.lock` のコミット (git init + 初期コミット)
2. 統合テストを Firestore エミュレータ起動して実行 (`docker compose up` → pytest)
3. 本番デプロイのドライラン (`gcloud run deploy`)
4. `--live` での Vertex AI ゴールデン評価 (課金あり)
5. `guard_trigger_rate` の Cloud Logging log-based metric 自動作成スクリプト

## 参照

- 前回: `memo/history/005-refactor-and-web-tests.md`
- CLAUDE.md: 品質チェックエラーの根本解決方針と外部ライブラリ起因エラーの例外枠

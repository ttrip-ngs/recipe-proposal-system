# 004: 評価ランナー接続・README・docker-compose・Firebase Web SDK

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `003-mvp-core-implementation.md`

## 概要

MVP コア実装後の周辺整備。評価ランナーを services と接続し、オンボーディング・統合テスト土台・本格的なログイン画面を整えた。

## 実施内容

### 評価ランナー本実装

- `evaluation/schema.py` 新規作成
  - `GoldenCase / GoldenInput / ExpectedProperties` などの Pydantic モデル
  - 既存 JSONL ゴールデンケース 8 件をそのままパース可能
- `evaluation/runner.py` 全面刷新
  - ゴールデンケース → `SuggestContext` 変換 (`_to_family_profile`, `_recent_history`)
  - `services.suggest_dinner` にフェイク LLM (既定) または Vertex AI (`--live`) で通す
  - `expected_properties` の検証:
    - `must_not_contain_allergen` - 各 dish の ingredient.allergen_tags と禁止セットが交差しないこと
    - `must_include_category` - 指定カテゴリがすべて揃うこと
    - `must_not_include_recent_dishes` - 直近料理名との重複なし
    - 提案自体が `succeeded=True` であること
  - `RunReport` は `run_id / prompt_version / total_cases / passed / failed / cases` 形式で JSON 出力

### README.md

- クイックスタート (前提ツール・セットアップ・起動)
- 主要コマンド (lint / test / 評価 / デプロイ)
- プロジェクト構成図
- 重要な設計原則サマリ
- docs/ 配下への導線

### docker-compose.yml

- `firebase-emulators` サービス: Firestore + Auth + UI の 3 エミュレータを node:20-bookworm-slim 上で `firebase-tools@13.29.1` で起動
- `--import / --export-on-exit` で `emulator-data/` に永続化
- ヘルスチェック設定 (Emulator UI の応答)

### 統合テスト土台

- `tests/integration/conftest.py`
  - エミュレータ疎通確認 (`socket.create_connection` で TCP チェック)
  - 未起動時は `pytest.skip` (CI でエミュレータ未起動でもパスする)
  - 環境変数 `FIRESTORE_EMULATOR_HOST` を強制セット
- `tests/integration/test_firestore_roundtrip.py`
  - family 書き込み + repository 読み出しの疎通
  - recipe 登録 + `list_recipes` 経由の正規化確認

### Evaluation ランナーの単体テスト

- `tests/unit/test_evaluation_runner.py`
  - `allergen_cases.jsonl` を `_evaluate_case` で評価
  - フェイク LLM の固定応答 (肉じゃが / ほうれん草 / 豆腐わかめ味噌汁) は
    甲殻類・卵・ナッツ・落花生を一切含まないため、当該カテゴリは全 pass するはず

### Firebase Web SDK ログイン画面

- `auth/login.html` を本実装に差し替え
  - Firebase Web SDK v10 modular を CDN (`https://www.gstatic.com/firebasejs/10.14.1/...`) から ES Module 経由でロード
  - `window.__FIREBASE_CONFIG__` で設定値を注入する想定 (Phase 2 で main.py のテンプレートコンテキスト経由で差し込み予定)
  - `signInWithPopup(GoogleAuthProvider)` → `getIdToken()` → `POST /session` のフロー
  - エミュレータ接続: `window.__FIREBASE_AUTH_EMULATOR__` があれば `connectAuthEmulator` を呼ぶ
  - 開発者向けの手動 ID トークン投入フォームは残置 (エミュレータ UI からコピーする用)

### 微修正

- `services/suggest_dinner.py` の未使用ダミー `SuggestDinnerReport` 削除
- 同ファイル未使用 import (`field`) 削除

## 既知の弱点と次手

1. **proposal_repository が services.suggest_dinner に依存**
   - 本来は services が repository に依存する方向だが、MVP では型のために一時的に逆向き
   - Phase 2 で repository は domain + dict を受ける形にリファクタリング予定
2. **Firebase config の注入経路未整備**
   - `window.__FIREBASE_CONFIG__` を main.py 経由で埋め込む仕組みは Phase 2
   - 本番デプロイ前に必要 (MVP 使い始めでは開発者向けフォームで暫定運用)
3. **uv.lock 未生成**
   - `uv sync` で自動生成されるが、リポジトリに commit する運用か選定中
   - Dockerfile/cloudbuild.yaml は `--frozen` を前提にしているため、CI 到達前に commit 必要
4. **recency の評価ケース G101/G102 はフェイク LLM では失敗する想定**
   - 現在の evaluation_runner テストは `allergen_cases.jsonl` のみ検証
   - `--live` で Vertex AI を使うと意味のある評価になる
5. **セキュリティ: セッション署名鍵**
   - 本番では Secret Manager → Cloud Run 環境変数で注入する運用を docs/operations.md §4 に従って実装する

## 動作確認パス (手動)

```
docker compose up -d
uv sync
uv run python scripts/seed_firestore.py --emulator
uv run uvicorn recipe_system.main:app --reload

# 別ターミナル
open http://localhost:8000/login
# Emulator UI で発行した ID Token を貼って /session -> / にリダイレクト
# 「献立を提案してもらう」→ フェイク LLM の肉じゃが献立が表示される
```

## 参照

- 計画: `~/.claude/plans/recipe-system-design-md-claude-md-curried-salamander.md`
- 前回: `memo/history/003-mvp-core-implementation.md`

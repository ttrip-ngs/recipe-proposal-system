# 016 検証環境での実 Vertex AI Claude 利用セットアップ

## 目的

検証環境で `USE_FAKE_LLM=false` とし、実 Vertex AI 経由 Claude (claude-sonnet-4-6) を
呼べるようにする。通常開発は Fake のままにし、検証時のみ実 LLM に切り替える構成とする。

## 対象 GCP プロジェクト

- プロジェクト ID: `<GCP_PROJECT_ID>` (表示名 `recipe-system`、番号 <GCP_PROJECT_NUMBER>)
  - ID がコマンド断片のように見えるが正規 ID。打ち間違いではない。
- gcloud 構成プロファイル: `recipe-proposal-system` (account: <作業用Googleアカウント>)
- ADC: <作業用Googleアカウント>、quota project を同プロジェクトに設定済み
- 権限: gcp-work は組織継承で editor/owner 相当 (API enable 成功で確認)。
  プロジェクト直下の owner は <管理者Googleアカウント>。

## 実施した変更

1. `aiplatform.googleapis.com` を有効化。
2. `src/recipe_system/config.py`: `Settings.model_config.env_file` を
   `os.getenv("ENV_FILE", ".env")` に変更。既定は従来どおり `.env`、
   検証時のみ `ENV_FILE=.env.staging` でロード先を切り替えられる (additive)。
3. `.env.staging` を新規作成 (.gitignore 済み)。実 Vertex `global` + Firestore
   エミュレータのハイブリッド。`USE_FAKE_LLM=false`、`VERTEX_AI_LOCATION=global`。
4. `.gitignore` に `.env.staging` を追加。
5. `tmp/smoke_vertex.py`: Firestore/予算ガードを経由せず VertexClaudeClient を
   直接叩く到達性確認スクリプト (tmp は gitignore)。

## リージョン決定: global エンドポイント

Anthropic 公式ドキュメント (Claude on Vertex AI) より:
- Claude は `global` エンドポイント推奨 (料金プレミアムなし・可用性最大)。
- リージョナル/マルチリージョン (`us`/`eu`) は 10% 割増。`asia` マルチリージョンは無い。
- asia-northeast1 で Claude が使える保証はないため、`VERTEX_AI_LOCATION=global` を採用。
- 本番で PII データ所在地 (residency) を厳格化したい場合のみ、リージョナル
  エンドポイント (+10%) を再検討する。コードは `vertex_ai_location` を
  AnthropicVertex の region に渡すだけなので env 変更で切替可能 (client.py:104)。

## 現状のブロッカー (ユーザー Console 作業が必要)

スモークテスト結果: HTTP 429 RESOURCE_EXHAUSTED。
```
Quota exceeded for aiplatform.googleapis.com/global_online_prediction_requests_per_base_model
with base model: anthropic-claude-sonnet-4-6.
```
新規プロジェクトのため Claude のクォータが 0。技術チェーン (API・認証・リージョン・
モデルID・コード) は全て通過済みで、残るは Google 側のクォータ付与のみ。

### 必要なアクション
1. Vertex AI Model Garden で Claude Sonnet 4.6 を Enable (Anthropic 利用規約同意)。
2. クォータが 0 のままなら IAM & Admin > Quotas で
   `global_online_prediction_requests_per_base_model`
   (base model: anthropic-claude-sonnet-4-6) の増加を申請。
   - global エンドポイントを使うので global 系メトリクスを対象にする
     (リージョナルとは別カウント)。

## クォータ付与後の残作業

- `firebase emulators:start --only firestore,auth` (または docker compose up)。
- `config/llm_budget.monthly_jpy_limit` がエミュレータに投入済みか確認
  (`scripts/seed_firestore.py` 既定 3000 円)。
- `ENV_FILE=.env.staging uv run python tmp/smoke_vertex.py` で直接到達性を再確認。
- `ENV_FILE=.env.staging uv run uvicorn recipe_system.main:app --reload` で
  単日提案を1回実行し、`llm_usage` に記録 (cost>0) が入ることを確認。

## 未対応 (フォローアップ)

- `docs/operations.md §10` の環境分離表は `recipe-system-dev`/`recipe-system-prod`
  想定のまま。実プロジェクトは `<GCP_PROJECT_ID>` なので要更新 (別 PR 想定)。

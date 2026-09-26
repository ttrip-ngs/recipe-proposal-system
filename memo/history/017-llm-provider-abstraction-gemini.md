# 017 LLM プロバイダ抽象化 (Claude / Gemini 切替対応)

## 背景

memo/016 で判明した通り、検証環境の GCP プロジェクトで Claude Sonnet 4.6 の
Vertex AI クォータが 0 のままで、Model Garden の Enable だけでは自動付与されず、
IAM & Admin > Quotas でのクォータ増加申請 (審査に数日〜数週間) が必要と判明した。

さらに増加申請を試みたところ、実行アカウント `<作業用Googleアカウント>` にプロジェクトの
IAM ロールが一切付与されていないことが判明 (プロジェクト Owner は `<管理者Googleアカウント>`
のみ)。`<管理者Googleアカウント>` から `roles/editor` を付与してもらい、Model Garden の
Claude Sonnet 4.6 の利用規約に同意 (Enable) したが、クォータ自体は依然 0 のまま
(Enable と クォータ付与は別物と判明)。クォータ増加申請の審査を待つ間、実 LLM 応答での
動作検証を進めるため、Vertex AI 上でデフォルトクォータが付与されている Gemini を
代替経路として使えるようにした。

## 実施した変更

1. `pyproject.toml`: `google-genai` を追加 (`uv add google-genai`)
2. `src/recipe_system/config.py`:
   - `llm_provider: Literal["fake","claude","gemini"] | None` (env: `LLM_PROVIDER`) 追加
   - `llm_model: str | None` (env: `LLM_MODEL`) 追加。プロバイダ共通のモデル名上書き用
   - `effective_llm_provider` プロパティ追加。`llm_provider` 明示があれば優先、
     未指定なら既存の `use_fake_llm` (bool) から `fake`/`claude` を導出 (後方互換)
3. `src/recipe_system/llm/client.py`:
   - `VertexGeminiClient` 新設。`google-genai` SDK の Vertex AI モードで実装。
     `response_mime_type="application/json"` を指定し JSON 出力を強制。
     Gemini 2.5 系の implicit caching に合わせ `USE_PROMPT_CACHE` は no-op。
     `usage_metadata.prompt_token_count` はキャッシュ分を含むため、
     Anthropic 流儀 (input はキャッシュ読み出しを含まない) に揃えて差し引く
   - `VertexClaudeClient` にも `model` 引数を追加し `LLM_MODEL` を尊重するよう変更
   - `build_raw_client(provider)` をファクトリとして抽出。`build_client` (予算ガード付き)
     と `evaluation/runner.py` の両方がこれを共用する
4. `src/recipe_system/llm/pricing.py`:
   - `gemini-2.5-flash` / `gemini-2.5-pro` の単価を `PRICING_TABLE` に追加
   - 未登録モデルは引き続きコスト 0 円だが、`fake-sonnet` / `unknown` 以外は
     `pricing.unknown_model` 警告ログを出すよう変更 (価格表への追加漏れ = 課金の
     過小計上に気づけるようにするため)
5. `evaluation/runner.py`: `VertexClaudeClient()` / `FakeVertexClient()` の直接構築を
   `_build_llm(use_live)` (内部で `build_raw_client` を使用) に置換。`--live` かつ
   provider が `fake` に解決される場合は `ValueError` で明示的に弾く
6. テスト追加: `tests/unit/test_gemini_client.py` (新規)、
   `tests/unit/test_client_factory.py` (新規)、`test_config.py` / `test_pricing.py` /
   `test_evaluation_runner.py` に関連ケース追加
7. ドキュメント: `docs/llm-integration.md` §1/§1.1 (新設)/§2/§4/§9/§10、
   `.env.sample`、`docs/adr/0005-pluggable-llm-provider.md` (新規)、`TASKS.md`

## テスト分離の副次的なバグ発見と対応

`tests/unit/test_config.py` は既存テストで `importlib.reload(config)` を使って
env var 変更を反映させている。一方 `recipe_system.llm.client` は
`from recipe_system.config import get_settings` でモジュールレベルに関数
オブジェクトを束縛しているため、reload 後は `config.get_settings` と
`llm.client` が握る `get_settings` が別オブジェクト (別の `lru_cache`) になる。
`tests/unit/test_budget_guard.py` のようにモジュールレベルで
`recipe_system.llm.client` を import しているファイルが collection 時点で
先に `llm.client.get_settings` を束縛してしまうため、test_config.py の
reload 後もその束縛は更新されない。

この結果、`config.get_settings.cache_clear()` だけをテストの前後で呼んでも、
`llm.client` 経由で `get_settings()` を呼ぶテスト (`VertexGeminiClient` /
`VertexClaudeClient` を構築するテスト) が、他のテストファイルの collection
順序次第で古いキャッシュ (env var 未反映) を参照してしまう場合があると判明した
(実際に `test_config.py` + `test_budget_guard.py` を含む組み合わせで再現)。

対応として、新規テストファイル (`test_client_factory.py`, `test_gemini_client.py`,
`test_evaluation_runner.py`) の autouse フィクスチャで `config.get_settings` と
`recipe_system.llm.client.get_settings` の両方の `lru_cache` を明示的にクリアする
ようにした。既存の `test_config.py` の `importlib.reload` パターン自体は
変更していない (スコープ外のため)。

## 使い方

```
# Gemini で実 LLM 検証 (Claude クォータ承認を待たずに動作確認)
ENV_FILE=.env.staging LLM_PROVIDER=gemini uv run python evaluation/runner.py \
    --golden evaluation/golden/*.jsonl --live

ENV_FILE=.env.staging LLM_PROVIDER=gemini uv run uvicorn recipe_system.main:app --reload

# Claude クォータ承認後は環境変数を変えるだけで戻せる
ENV_FILE=.env.staging LLM_PROVIDER=claude uv run uvicorn recipe_system.main:app --reload
```

## 実 Gemini スモークテストで発見・修正した不具合

`ENV_FILE=.env.staging LLM_PROVIDER=gemini uv run python -m evaluation.runner
--golden evaluation/golden/allergen_cases.jsonl --live` で実 API 疎通確認を行った
ところ、3 ケース中 3 ケースとも `LLMResponseParseError` (JSON デコード失敗) で
失敗した。原因を切り分けたところ、Gemini 2.5 Flash は既定で thinking (内部思考)
が有効で、そのトークンも `max_output_tokens` の予算を消費する仕様であり、
`max_tokens=2000` のうち **1916 トークンが thinking に消費され** (`finish_reason:
MAX_TOKENS`)、可視 JSON がわずか 144 文字で打ち切られていた。

対応として `VertexGeminiClient.generate()` の `GenerateContentConfig` に
`thinking_config=types.ThinkingConfig(thinking_budget=0)` を追加し、thinking を
無効化した。同一プロンプトで再検証したところ 3 ケース中 3 ケースとも正常な JSON
が返り、パース・ガードレール検証まで一気通貫で成功することを確認した。

本タスク (献立提案の構造化 JSON 生成) は長い推論を要さないため thinking 無効化
は妥当だが、将来 Gemini により複雑な判断をさせるユースケースを追加する場合は
`thinking_budget` を再検討すること。

全ゴールデンセット (`evaluation/golden/*.jsonl`, 11 ケース) で `--live` 再実行した
結果、単日提案 (allergen/dislike/recency, 8 ケース) は全て実 Gemini でパスした。
週間提案 (`weekly_basic_cases.jsonl`, W001-W003) は 3 ケースとも JSON デコード
失敗で落ちたが、これは thinking とは無関係の**別の既存の問題**と判明した:
`services/weekly_planner.py` は `llm.generate()` 呼出時に `max_tokens` を
オーバーライドしておらず、既定値 `2000` のまま 7 日 x 3 品の JSON を生成させて
いる。`docs/llm-integration.md` §2 (Phase B) には元々「最大トークン数は
5000-6000 程度を目安とする (将来 max_tokens オプション化)」と TODO 化されて
いた箇所であり、本 PR (LLM プロバイダ抽象化) のスコープ外の既存課題のため
今回は修正せず、`TASKS.md` にフォローアップとして記録するに留める
(Claude 経由でも同じ理由で truncate するはずだが、これまで `--live` で
実際に検証されたことがなく気づかれていなかったと考えられる)。

## 後の開発に必要な注意

- Gemini は一時的な代替経路であり、恒久的な本番プロバイダは Claude を想定する
  ([[adr-0005]] 参照)。Claude クォータ承認後は `.env.staging` の
  `LLM_PROVIDER` を `claude` に戻す運用とする
- `gemini-2.5-pro` の 200K トークン超過帯の単価は `pricing.py` に未登録
  (本システムのプロンプトは遥かに小さいため実害なし、コメントで注記のみ)

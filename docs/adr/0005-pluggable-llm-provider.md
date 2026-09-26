# ADR 0005: LLM プロバイダの抽象化 (Claude / Gemini 切替)

- Status: Accepted
- Date: 2026-07-04

## Context

本システムは Vertex AI 経由 Claude Sonnet 4.6 を LLM 提案層に固定利用する設計としてきた (`docs/llm-integration.md` §1)。運用中の GCP プロジェクトで Claude Sonnet 4.6 のクォータが 0 (新規プロジェクトのため) であることが判明し、Google Cloud Console でのクォータ増加申請の審査に数週間かかる見込みとなった。

その間も実 LLM 応答での動作検証 (JSON 出力形式の妥当性、ガードレール連携、UI の実データ表示等) を行いたい。Vertex AI の Gemini モデルは同一プロジェクトでデフォルトクォータが付与されており、即座に実 LLM 検証が行える。

一方で、恒久的な本番プロバイダは引き続き Claude を想定しており、この変更を「Claude から Gemini への移行」ではなく「一時的な代替経路の追加」として設計する必要がある。

## Decision

`src/recipe_system/llm/client.py` の `LLMClient` Protocol (`generate(system, user, prompt_version, max_tokens) -> LLMResponse`) は元々プロバイダ非依存に設計されていたため、これを維持したまま以下を追加する。

- `VertexGeminiClient` を新設 (`google-genai` SDK 経由、Vertex AI モード)。`LLMClient` Protocol を満たすのみで、既存の `FakeVertexClient` / `VertexClaudeClient` と対等に扱う
- `Settings` に `llm_provider: Literal["fake", "claude", "gemini"] | None` (env: `LLM_PROVIDER`) と `llm_model: str | None` (env: `LLM_MODEL`) を追加。`effective_llm_provider` プロパティで、未指定時は既存の `USE_FAKE_LLM` (bool) から後方互換的に導出する
- `build_raw_client(provider)` をファクトリとして抽出し、`build_client` (予算ガード付き) と `evaluation/runner.py` の `--live` 経路の両方がこれを共用する。プラグインレジストリのような汎用機構は導入せず、KISS に則り if/elif の単純な分岐に留める
- 料金表 (`llm/pricing.py`) に Gemini 2.5 Flash / Pro の単価を追加。未登録モデルは引き続きコスト 0 円だが、`fake-sonnet` / `unknown` 以外は警告ログを出し、価格表への追加漏れ (課金の過小計上) に気づけるようにする

ガードレール (アレルゲン検証、`src/recipe_system/guardrails/`) はプロバイダに関わらずコード側の決定論的処理のままであり、本変更では一切触れない。プロンプト YAML (`llm/prompts/*.yaml`) もプロバイダ非依存の日本語テキストであり、バージョン変更は不要と判断した。

## Consequences

### 正の影響

- Claude クォータ承認を待たずに実 LLM (Gemini) での動作検証が可能になる
- `LLM_PROVIDER` 環境変数の変更のみでプロバイダを切り替えられ、コード変更・再デプロイが不要
- 予算ガード (`BudgetGuardedClient`)・使用量記録 (`llm_usage`)・`/admin/usage` 画面はプロバイダ非依存のまま機能する (料金表の追加のみで対応)
- 将来的に他プロバイダを追加する場合も同じ `LLMClient` Protocol に従うクラスを 1 つ追加し、ファクトリに分岐を足すだけで済む

### 負の影響

- 料金表・プロンプトキャッシュの挙動 (Claude は明示 `cache_control`、Gemini は implicit caching で `USE_PROMPT_CACHE` が no-op) がプロバイダ間で異なり、ドキュメント・運用者の理解コストが増える
- `google-genai` という新規依存が増える
- 未登録モデルの警告ログ機構を追加したことで、意図的な未登録 (テスト用モデル名等) には `_ZERO_COST_MODELS` への追加が必要になる

### リスクと緩和策

- **Gemini の JSON 出力が途中で切れる**: 実 Gemini スモークテスト (ゴールデンセット評価の `--live` 実行) で、Gemini 2.5 の thinking (内部思考) トークンが `max_output_tokens` 予算の大半を消費し可視 JSON が途切れる不具合を検出した。`GenerateContentConfig` に `thinking_config=ThinkingConfig(thinking_budget=0)` を指定して解消した (`memo/history/017` 参照)。`response_mime_type="application/json"` と合わせて、それでも稀に壊れる場合は実 Gemini スモークテストで継続的に検知する運用とする
- **恒久的に Gemini へ固定運用されてしまうリスク**: 本 ADR で「一時的な代替経路」と明記し、Claude クォータ承認後は `LLM_PROVIDER=claude` に戻す運用を `docs/llm-integration.md` §1.1 に記載する

## 参照

- [llm-integration.md](../llm-integration.md) §1, §1.1, §4, §9, §10
- [guardrails.md](../guardrails.md) (本変更で不変であることの根拠)
- `memo/history/017-llm-provider-abstraction-gemini.md`

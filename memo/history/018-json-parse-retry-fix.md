# 018 ローカルでの実 Gemini 検証と JSON パース失敗リトライの修正

## 背景

memo/017 で導入した LLM プロバイダ抽象化のローカル検証として、
`ENV_FILE=.env.staging LLM_PROVIDER=gemini` で `suggest_dinner` サービスを
実 Gemini 相手に複数回実行したところ、8 回に 1 回程度の頻度で
`LLMResponseParseError` (JSON デコード失敗) が発生し、そのまま例外が
呼出元に伝播することを確認した。

`docs/llm-integration.md` §7 には元々「JSON パース失敗: 1 回だけ再要求」と
記載されていたが、実際の `services/suggest_dinner.py` / `services/weekly_planner.py`
のコードにはこのリトライが実装されておらず、ガードレール違反 (block) の
リトライのみが実装されていた。ドキュメントとコードの乖離であり、
Claude・Gemini どちらのプロバイダでも起こりうる (これまで実 LLM で
本番コードパスがテストされたことがなく気づかれていなかった)。

## 実施した変更

- `services/suggest_dinner.py`: `_attempt_with_parse_retry()` を新設し、
  `_single_attempt()` を `LLMResponseParseError` 発生時のみ 1 回だけ
  同一プロンプトで再試行するようにラップ。初回呼出・ガードレールリトライ
  呼出の両方をこのラッパー経由に変更
- `services/weekly_planner.py`: 同様に `_attempt_with_parse_retry()` を新設
- `docs/llm-integration.md` §7: 実装の実態 (同一プロンプトで再要求、
  ガードレールリトライとは独立) に合わせて記述を修正
- テスト追加: `tests/unit/test_suggest_dinner.py` /
  `tests/unit/test_weekly_planner.py` に `_RawTextScriptedClient` を追加し、
  (1) 1 回目パース失敗 → 2 回目成功、(2) 2 回連続失敗 → 例外伝播、の
  2 パターンを検証

## 設計判断

- パース失敗リトライとガードレール違反リトライは独立した仕組みとした
  (最悪ケースで 1 回の `suggest_dinner`/`plan_week` 呼出につき最大 4 回の
  LLM 呼出になりうるが、パース失敗自体が稀であるため実運用上の影響は小さい
  と判断)
- リトライ時にプロンプト内容を変更する (例: 「JSON のみを返してください」の
  文言追加) ことは行わず、同一プロンプトでの再試行のみとした。実 Gemini
  検証で、単純な再試行だけで大半のケースが解消することを確認済み。将来
  再試行後も高頻度で失敗するようなら、リトライ時に補正文言を追加する
  拡張を検討する

## 検証

- `uv run pytest` 125 件通過 (新規 4 件含む)
- `ruff check` / `ruff format` / `mypy` 全通過
- `ENV_FILE=.env.staging LLM_PROVIDER=gemini` で `suggest_dinner` を 8 回
  連続実行し全成功 (thinking_budget=0 の効果と合わせて確認)

## 後の開発に必要な注意

- この修正は memo/017 (LLM プロバイダ抽象化) の直後にローカル検証で発見した
  ため、別コミットとして追加した。ガードレール (アレルゲン検証) には
  一切触れていない
- `services/weekly_planner.py` の `max_tokens` 未指定問題 (TASKS.md 記載の
  既知の課題) は本修正とは別の問題であり、引き続き未対応

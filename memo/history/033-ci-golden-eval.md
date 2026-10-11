# 033 フェイク LLM のゴールデン評価を CI に追加し、評価メトリクスを実装

2026-10-11

## 背景

027 で CI を整備した時点では、フェイク LLM のゴールデン評価は G101 (直近重複) が常に落ちる
(10/11) ため CI に入れず、実 LLM の評価 (prompt-eval.yml) も手動起動のみだった。そのため
ランナー・サービス層・ケース定義の回帰を PR で検出する仕組みが無かった。また
docs/evaluation.md と CLAUDE.md 6.3 が前提とする metrics (guard_trigger_rate ほか) が
evaluation/runner.py に未実装だった。

方針 (ユーザー指示): 自動テスト・CI では実 LLM を呼ばない (API 料金が本番利用の何倍にもなる)。
実 LLM の評価は prompt-eval.yml の手動起動のみのまま。手動で --live を使う場合も事前に了承を得る。
本作業では --live を一度も実行していない (プロンプトは変更していない)。

## G101 の原因と修正

- 原因: FakeVertexClient の単日応答が、プロンプトに埋め込まれた直近履歴・候補レシピを読まず、
  常に「肉じゃが / ほうれん草のおひたし / 豆腐とわかめの味噌汁」を返していた。G101 は直近
  7 日に肉じゃがを含むため、フェイクでは原理的に通らなかった
- 修正: フェイクがレンダリング済みプロンプトから「直近 N 日に作った料理: a / b」行と候補レシピ行
  (`- 名前 (カテゴリ / 主食材: x)`) を読み取り、カテゴリごとに「候補レシピ -> 既定プール」の順で
  直近に無い最初の料理を選ぶ。骨子 (週間) も直近を除き、前日と主食材が重ならないよう選ぶ
- 既定プールの食材は辞書登録済みのものだけにした (旧ローテーションの「ぶり」は未登録だった)。
  未登録食材を混ぜると unknown_ingredient_rate がフェイク都合で上がるため。テストで固定
- ケースの削除・期待値の緩和はしていない。既存テスト (直近 7 日の履歴除外) は「肉じゃがが
  出ない・候補の鶏の照り焼きが出る」のアサートを追加して強化した

## 併せて見つけた不具合

週間ケースの評価が --live なしでも `resolve_detail_model()` 経由で `get_settings()` を呼び、
.env の無い環境 (CI、このワークスペース) では Settings の必須項目不足で例外になっていた。
詳細モデルの解決を --live 時に限定した。

## metrics

`compute_metrics` で 1 ケース = 1 提案 (週間は 7 日分で 1 提案) として算出する。

- guard_trigger_rate: retry_count >= 1 (初回 block で再依頼) の提案数 / 提案数
- warn_rate: 最終提案 (週間は全日) に warn 違反がある提案数 / 提案数
- unknown_ingredient_rate: `NormalizerDictionary.is_known` が偽の食材を含む提案数 / 提案数

例外で提案が得られなかったケースは分母から外し、分母を `metrics.proposals` に出す。
ケースごとの `unknown_ingredients` は辞書の週次レビュー (CLAUDE.md 6.6) にも使える。
フェイク LLM での値は 11 件すべて 0.0。

## CI

- ci.yml に `golden eval (fake LLM)` ジョブを追加。全件合格が必須で、`--summary` で
  metrics と不合格ケースを job summary に出す。レポート JSON はアーティファクトに保存
- prompt-eval.yml (手動起動・実 LLM) にも同じ job summary を出す
- ブランチ保護の required checks への追加は管理操作のため未実施 (ユーザー判断)

## 積み上げ PR に CI が走らない件

`pull_request` の CI は PR のマージ結果にある定義で動くため、base と head の両方が CI 導入前の
main から分岐した積み上げ PR (#5〜#7) ではチェックが報告されない。pull_request_target は
base 側の定義、schedule などはデフォルトブランチ (main, `.github` 無し) の定義で動くため、
ワークフロー側では対処できない。docs/development.md §12 に「一番下のブランチから dev を
merge して上へ伝播させる」運用を書いた。他ワークスペースのブランチは変更していない。

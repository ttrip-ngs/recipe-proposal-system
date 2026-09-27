# 027 GitHub 公開と GitHub Actions による CI 整備

2026-09-27

## 背景

並行していたブランチ (買い物リスト重複排除、食材価格 Phase 1、GCP デプロイ準備、
Opus 5.5 週間提案) を dev に統合し、GitHub (public) へ公開した。公開にあたり GCP の
プロジェクト ID・番号・Google アカウントのメールアドレスを伏せ字にし、履歴は 1 コミットに
まとめた (全履歴はローカルのタグ `archive/dev-full-history` のみに残す. push しない)。

remote ができたので、operations.md §9.1 の「Cloud Build に一本化、GitHub Actions は併用しない」
(remote が無い前提の判断) を見直し、PR の品質ゲートを GitHub Actions に置いた。

## 構成

- `ci.yml`: lint (pre-commit 全フック・全履歴 gitleaks・mypy) / test (エミュレータ込み pytest と
  ガードレールのカバレッジ 95% 以上) / docker build
- `prompt-eval.yml`: 実 LLM のゴールデンセット評価. 手動起動のみ
- `dependabot.yml`: Action (SHA 固定) と uv 依存を dev 向けに週次更新
- デプロイは従来どおり Cloud Build の手動実行

## 判断の記録

- pre-commit の gitleaks フックは `--staged` でステージ済み差分しか見ず、CI では空振りする。
  リリースバイナリをチェックサム検証して履歴全体を走査するステップを別に置いた。
  Docker イメージ版は worktree や所有者チェックで git を読めず「0 bytes」で成功するため使わない
- フェイク LLM での評価は固定献立のため G101 (直近重複) が必ず落ちる。CI の評価は実 LLM に限定
- 当初は全 PR で起動しプロンプト変更時だけ実 LLM を呼ぶ形にしたが、API 料金が二重にかかる
  (6.3 でローカル評価が必須) ため、手動起動のみに変更し必須チェックからも外した
- docker-compose のヘルスチェックが wget を使っていたが、イメージに wget / curl が無く常に
  unhealthy だった。node で Firestore (8080) と Auth (9099) の応答を確認する形に修正

## 残課題

- CI で評価を手動起動したくなったら、リポジトリ Secret `ANTHROPIC_API_KEY` を登録する

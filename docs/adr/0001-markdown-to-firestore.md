# ADR 0001: データ永続化を Markdown+Git から Firestore に変更

- Status: Accepted
- Date: 2026-04-18

## Context

当初の設計書（`recipe-system-design.md` §2）では、家族プロファイル・レシピ・履歴を Markdown + Git で管理し、LLM に渡すときは全文を concat する方針を推奨していた。この方式は以下のメリットがある。

- Git による差分追跡
- 既存の chezmoi / Neovim / WezTerm ワークフローとの親和性
- RAG 不要でホリスティック判断が可能
- LLM のコンテキストウィンドウに余裕で収まるデータ量

一方で、実装に着手するにあたり以下の課題が顕在化した。

- Cloud Run からの読み書きに Git リポジトリを clone/pull する処理が必要で、起動レイテンシが増える
- 家族複数人が同時に編集した場合のコンフリクト解決が難しい
- Firebase Authentication との統合がアプリ側で煩雑になる
- バックアップ手段が Git のリモートに依存し、GCP 内完結の運用から外れる
- 提案ログ・フィードバックなど書き込み頻度の高いデータを Git で扱うのは不適切

## Decision

データ永続化のマスタを Firestore Native Mode に統一する。ただし以下を妥協点として残す。

- `data/seeds/` に Markdown/YAML 形式のサンプルを置き、初期投入スクリプト（`scripts/seed_firestore.py`）で Firestore に流し込む
- 将来 Firestore からエクスポートして MD ベースに戻したくなった場合のエクスポート機能を Phase 2 で用意する
- プロンプト YAML など「LLM に渡す不変データ」は Firestore ではなく Git 管理を継続する

## Consequences

### 正の影響

- Cloud Run・Firebase Auth・Vertex AI と同じ GCP プロジェクト内でシームレスに統合できる
- IAM・請求・ログが GCP 内で一元化される
- 家族複数人の同時書き込みが安全になる
- 提案ログ・履歴・フィードバックを大量書き込みできる
- Cloud Scheduler による週次 export でバックアップ簡素化（[operations.md](../operations.md) §7）

### 負の影響

- Git diff による履歴追跡機能を失う（Firestore の変更履歴機能と提案ログで代替）
- NoSQL 特有のスキーマ設計を意識する必要がある（[data-model.md](../data-model.md)）
- 完全オフライン開発は Firebase エミュレータに依存する

### リスクと緩和策

- **Firestore 依存による GCP ロックイン**: データモデルをシンプルに保ち、移行スクリプトを用意することで他 NoSQL・RDB への移行余地を残す
- **家族情報のセンシティブ性**: Firestore セキュリティルール（[data-model.md](../data-model.md) §4）と Secret Manager 運用で最小権限化

## 参照

- [data-model.md](../data-model.md)
- [operations.md](../operations.md)
- [recipe-system-design.md](../../recipe-system-design.md) §2

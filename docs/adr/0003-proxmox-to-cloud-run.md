# ADR 0003: デプロイ先を Proxmox LXC から Cloud Run に変更

- Status: Accepted
- Date: 2026-04-18

## Context

当初の設計書（`recipe-system-design.md` §11）では、Proxmox 上の LXC または VM にデプロイする方針だった。これは既存の内部インフラ（Authentik、Zabbix、Grafana、Loki）との同居を想定したもの。

一方で以下の理由から、Cloud Run への変更を検討した。

- Vertex AI と同じ GCP プロジェクト内に集約することで IAM・ネットワーク・請求が統一される
- Firestore・Firebase Auth と組み合わせた GCP フルマネージド構成が低予算で成立する
- `min-instances=0` により家庭用途（月 30〜90 回呼び出し）では無料枠内に収まる
- Proxmox 側の運用負荷（OS アップデート・ネットワーク・証明書管理）を避けられる
- 家族向けの小規模サービスに対して Proxmox は過剰装備

## Decision

デプロイ先を Cloud Run（`asia-northeast1`）に変更する。Proxmox インフラとの連携は行わず、既存スタック（Authentik・Zabbix・Grafana・Loki）も Phase 1 では使わない。

- 実行環境: Cloud Run（`min-instances=0`、`max-instances=3`）
- 認証: Firebase Authentication（Authentik は使わない）
- ログ: Cloud Logging（Loki への転送は Phase 2 以降に検討）
- 監視: Cloud Monitoring（Zabbix 連携は Phase 2 以降に検討）
- コンテナレジストリ: Artifact Registry（asia-northeast1）
- CI/CD: Cloud Build または GitHub Actions（[operations.md](../operations.md) §9 で確定）

## Consequences

### 正の影響

- インフラ運用負荷がほぼゼロになる（OS パッチ・ネットワーク・証明書不要）
- スケール自動化・TLS 終端・ロードバランサ等が標準提供される
- コスト: 使わない時間帯はゼロ課金。想定月額 300〜600 円（[operations.md](../operations.md) §8）
- Vertex AI・Firestore・Firebase Auth との統合がシンプル

### 負の影響

- Proxmox 上の既存運用ツール群（Zabbix・Grafana・Loki）と切り離される
- Cloud Run の cold start による初回レイテンシ（2〜5 秒）が発生する（対策は [ui-spec.md](../ui-spec.md) §6）
- GCP ロックインが進む（Firestore 単体での脱出はハードル高）

### 緩和策

- cold start: Prompt Caching 併用と UX（「考え中」ページ + polling）で体感を改善
- ロックイン: データモデルを単純に保ち、移行スクリプトを用意して退路を確保
- 既存スタックとの将来統合: Phase 2 以降に Cloud Logging → Loki 転送など検討

### 採用しなかった代替案

- **Cloud Functions (2nd gen)**: LLM 呼び出し（10 秒前後）には実行時間制約が窮屈
- **GKE Autopilot**: 家庭規模には過剰装備でコストも上がる
- **Compute Engine + LXC の併用**: 運用負荷が残る

## 参照

- [architecture.md](../architecture.md)
- [operations.md](../operations.md)
- [ui-spec.md](../ui-spec.md)

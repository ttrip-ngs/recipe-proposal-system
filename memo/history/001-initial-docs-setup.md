# 001: 基本設計ドキュメント群の初期セットアップ

- 日付: 2026-04-18
- 担当: Claude Code（Opus 4.7）+ ユーザー

## 概要

既存の設計ディスカッション成果物 `recipe-system-design.md` を踏まえ、Claude Code 向けの指針（CLAUDE.md）と `docs/` 配下の基本設計ドキュメントを整備した。

## 実施内容

### 技術スタックの確定（ディスカッションで変更）

| 項目 | 設計書原案 | 確定 |
|---|---|---|
| Web フレームワーク | Flask + SQLAlchemy | FastAPI + Jinja2 SSR |
| 実行環境 | Proxmox LXC/VM | Cloud Run（asia-northeast1） |
| 認証 | Authentik SSO | Firebase Authentication |
| データ永続化 | MD + Git | Firestore Native Mode |
| Prompt Caching | Phase 2 想定 | MVP から採用 |

上記の変更経緯は `docs/adr/0001-0003` に記録。

### 作成ファイル

- `CLAUDE.md`（リポジトリ直下）
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/llm-integration.md`
- `docs/guardrails.md`
- `docs/evaluation.md`
- `docs/operations.md`
- `docs/ui-spec.md`
- `docs/development.md`
- `docs/glossary.md`
- `docs/adr/0001-markdown-to-firestore.md`
- `docs/adr/0002-flask-to-fastapi.md`
- `docs/adr/0003-proxmox-to-cloud-run.md`

### Phase 1 MVP スコープ

`docs/architecture.md` §7 に Phase 境界表として明示。MVP は以下に絞った。

- 家族プロファイル（閲覧のみ、編集は Phase 2）
- 定番レシピ格納（100 品程度、seed で投入）
- 履歴記録・直近 7 日除外フィルタ
- Sonnet 単体での夕食提案（Haiku 分担は Phase 2）
- 食材正規化辞書（消費者庁 28 品目ベース）
- 決定論的アレルゲン検証 + 1 回リトライ
- Firebase Auth ログイン
- FastAPI + Jinja2 SSR「提案 → 採用 → フィードバック 3 択」
- 提案ログ全保存
- ゴールデンセット最小版（5〜10 ケース）と評価 CLI
- Prompt Caching
- Cloud Run デプロイ

Phase 2 送り: 買い物リスト生成・在庫管理 UI・Haiku 分担・Grafana 連携・朝食昼食・事後フィードバック・Opus 週間プラン。

### 今後の開発で参照すべきポイント

1. アレルゲン検証のコードによる担保は絶対（CLAUDE.md §6.1）
2. プロンプト変更時は `[prompt]` プレフィックス + ゴールデン評価（CLAUDE.md §6.3）
3. Firestore スキーマ変更は `docs/data-model.md` 同時更新 + migration スクリプト（CLAUDE.md §6.4）
4. 未知食材はデフォルト `warn` で通す。`block` にしない（CLAUDE.md §6.6）
5. ガード違反リトライは最大 1 回、フェイルオープン禁止（CLAUDE.md §6.7）

### 未着手・次のアクション候補

- リポジトリの pyproject.toml / .pre-commit-config.yaml / Dockerfile 雛形作成
- firebase.json / firestore.rules / firestore.indexes.json 雛形作成
- `src/recipe_system/` パッケージ骨格の作成
- 食材正規化辞書（消費者庁 28 品目ベース）の aliases.yaml / allergens.yaml 初期版
- 家族プロファイルサンプル `data/seeds/family.example.yaml`
- ゴールデンセット初期 JSONL 雛形
- scripts/seed_firestore.py の骨格
- evaluation/runner.py の骨格

## 参考

- 原典: `recipe-system-design.md`
- 計画ファイル: `~/.claude/plans/recipe-system-design-md-claude-md-curried-salamander.md`

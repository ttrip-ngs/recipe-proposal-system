# 007: git 初期化と Firebase Emulator の Java 依存把握

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `006-test-run-and-type-fixes.md`

## 概要

リポジトリを git 化して初期コミットを打ち、dev ブランチに切り替えた。Firebase Emulator Suite の Java 依存を実行時に把握し、開発環境ドキュメントに反映した。

## 実施内容

### git 初期化

```
git init -b main
uv tool install pre-commit
pre-commit install
```

初期コミット時の pre-commit で 2 件ひっかかり、以下を修正した上で commit 成立:

1. `trailing-whitespace` フックが `recipe-system-design.md` の末尾空白を自動修正
2. カスタムフック `prompt-yaml-version-check` が `python` コマンド未発見でエラー
   → `.pre-commit-config.yaml` で `entry: python ...` を `entry: uv run python ...` に変更
   (本プロジェクトは uv 管理前提なのでこちらに統一)

コミット後:
- main ブランチに初期コミット (commit `f2666a8`)
- `git branch dev` + `git checkout dev` で以降の作業を dev 上で行う
- Co-Authored-By: Claude Opus 4.7 (1M context) を付与

### Firebase Emulator の Java 依存を発見

ローカルで Firestore エミュレータを起動しようとしたところ、npx 経由の firebase-tools は動作したが以下のエラー:

```
Error: Process `java -version` has exited with code 1.
Please make sure Java is installed and on your system PATH.
```

#### 対応

- `docs/development.md` の前提ツール表に `Java 11+ (Firebase Firestore/Auth が要求)` を追加し、macOS なら `brew install --cask temurin` を推奨
- `README.md` にも同じ項目を追加
- `docker-compose.yml` のベースイメージを `node:20-bookworm-slim` から `andreysenov/firebase-tools:13.29.1` に変更
  (公式 node イメージは Java 非搭載で Firestore エミュレータが起動しない)
- Docker 経由なら Java は不要 (イメージに同梱)

### integration テストの挙動

`tests/integration/conftest.py` の `_require_emulator` フィクスチャは、エミュレータ未起動時は skip する設計になっていたため、Java 不在の環境では想定どおり skip された。

```
$ uv run pytest tests/integration
2 skipped in 0.01s
```

integration テスト自体の修正は不要。

### コミット内容

- 112 ファイル
- uv.lock (1179 行) も含めて初期コミット
- pre-commit すべて pass (gitleaks / trailing-whitespace / yaml / json / merge-conflict / large-files / ruff / ruff-format / prompt-yaml-version-check)
- ruff / mypy / pytest すべて green

## 残課題・次手

1. **本番 Firebase Web SDK 設定の投入**: `FIREBASE_WEB_CONFIG_JSON` を Firebase Console で取得して .env に設定 (本番のみ)
2. **Java を入れた上での integration テスト再走**: Temurin 導入後 `firebase emulators:start` → `uv run pytest tests/integration`
3. **gcloud run deploy --source .` のドライラン**: Artifact Registry リポジトリ作成 + Cloud Run デプロイの初回確認
4. **リモートリポジトリ設定**: `git remote add origin` して `git push -u origin main dev` する運用
5. **GitHub Actions (Cloud Build 併用時)**: CI を本格稼働させる

## 参照

- 前回: `memo/history/006-test-run-and-type-fixes.md`
- 初期コミット: `f2666a8 初期コミット: 家庭内レシピ提案システム MVP`

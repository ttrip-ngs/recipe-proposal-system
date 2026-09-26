# ADR 0002: Web フレームワークを Flask から FastAPI に変更

- Status: Accepted
- Date: 2026-04-18

## Context

当初の設計書（`recipe-system-design.md` §11）では、Flask + SQLAlchemy を採用する方針だった。これは既存の内部ツール方針に合わせる意図があった。

しかし以下の要件を満たすには FastAPI の方が自然だった。

- 非同期 LLM 呼び出し: Phase 2 で Sonnet + Haiku の並列実行（`asyncio.gather`）を行う。Flask は本来同期フレームワークで非同期対応は後付け
- Pydantic スキーマ検証: ガードレール・LLM 出力・API 入出力すべてで Pydantic v2 を活用したい
- 型ヒント重視: 現代的な Python の書き方と相性が良い
- 自動生成される OpenAPI: 将来 API 単体公開を検討する場合のメリット

Flask が優れている領域（テンプレートエコシステム・既存実績）もあるが、本プロジェクトは LLM 中心で API・型・非同期が主軸となるため、FastAPI の利点の方が大きい。

## Decision

Web フレームワークを FastAPI + Jinja2 SSR に変更する。SPA 化は Phase 1 では行わない（SSR で十分）。

- ルーターは `src/recipe_system/web/routes/` に配置
- テンプレートは `src/recipe_system/web/templates/`
- セッション管理は `itsdangerous` ベースの `SessionMiddleware` を利用
- 非同期処理は `BackgroundTasks`（MVP）、将来的に Celery 等へ移行可能な構造にする

## Consequences

### 正の影響

- LLM 並列呼び出しの実装が自然になる
- Pydantic による入出力検証がガードレールと統合できる
- 型ヒントが活きて mypy との相性が良い
- 開発サーバ（uvicorn）のホットリロードが高速

### 負の影響

- 既存内部ツール（Flask 中心）との統一性を失う
- Jinja2 を FastAPI で使う場合のボイラープレート（`Jinja2Templates` のインスタンス化等）が追加で必要

### 採用しなかった代替案

- **Flask + async**: 非同期サポートはあるが、本来設計が同期向けで習熟コストと実装上の罠が増える
- **Django**: 本プロジェクトには過剰装備
- **Starlette 単体**: FastAPI の上位互換的な低レベル。生産性は FastAPI が上

## 参照

- [architecture.md](../architecture.md)
- [ui-spec.md](../ui-spec.md)
- [llm-integration.md](../llm-integration.md)

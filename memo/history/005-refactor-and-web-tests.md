# 005: 依存方向リファクタ・Firebase config 注入・Web テスト

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `004-evaluation-and-devtools.md`

## 概要

MVP コア実装の弱点整理。レイヤー依存方向の是正、Firebase Web SDK config の本番注入経路、そして FastAPI TestClient ベースの Web ルーター結合テストを追加した。

## 実施内容

### proposal_repository の依存方向リファクタ

**Before**: `proposal_repository.save_proposal(proposal: DinnerProposal)` - repository が services に依存。レイヤー依存の逆転。

**After**: repository は dict を受ける純粋な IF に変更。
- `save_proposal_record(client, family_id, record: dict, requested_at)` - 新規保存
- `update_proposal_record(client, proposal_id, record: dict)` - pending → ready 更新
- `pending_placeholder_record()` - pending 用プレースホルダ
- `get_proposal / mark_accepted` は変更なし

services 側で `DinnerProposal.to_firestore_record()` を追加し、シリアライズはドメイン/サービス側の責務にした。Web ルーター (`web/routes/proposals.py`) もこの新 IF に合わせて書き換え。

これで repository は domain / dict のみに依存し、services / web にはまったく依存しなくなった (依存グラフが単方向)。

### Firebase Web SDK config 注入経路

**環境変数**
- `FIREBASE_WEB_CONFIG_JSON`: Firebase Console の web app config を JSON 1 行で投入 (apiKey / authDomain / projectId / appId 等)
- `FIREBASE_AUTH_EMULATOR_HOST_BROWSER`: ブラウザ側から見た Auth エミュレータ URL (通常 `http://localhost:9099`)

**実装**
- `config.Settings.firebase_web_config_json` (生文字列) + `firebase_web_config` (dict 変換プロパティ)。壊れた JSON は None に落とす
- `web/routes/auth.login_page` がテンプレートコンテキストに `firebase_config` と `firebase_auth_emulator_host_browser` を渡す
- `auth/login.html` 冒頭で `window.__FIREBASE_CONFIG__` / `window.__FIREBASE_AUTH_EMULATOR__` に JSON 埋め込み (`{{ ... | tojson }}` で安全にエスケープ)
- `.env.sample` にコメント付きで記載

本番では `FIREBASE_WEB_CONFIG_JSON` を Secret Manager から Cloud Run の環境変数に注入する運用 (既存 `operations.md` §4 と整合)。

### FastAPI TestClient 結合テスト

`tests/unit/test_web_routes.py` (Firestore 非依存のルーターのみ):
- `GET /healthz` → 200 + JSON
- `GET /` (未認証) → 303 `/login` リダイレクト
- `GET /login` → 200 + ログイン画面 HTML
- `GET /does-not-exist` → 404 のカスタムエラーページ
- `POST /logout` → 303 `/login`
- `GET /` (認証済み) → 200 + トップページ (`current_user` を DI オーバーライド)
- `POST /session` に不正トークン → 非 200

`tests/unit/test_config.py`:
- `FIREBASE_WEB_CONFIG_JSON` 未設定で None
- 有効 JSON で dict にパース
- 壊れた JSON で None に落ちる
- `FIRESTORE_EMULATOR_HOST` があれば `is_emulator=True`

Firestore を要するルート (`/proposals*`, `/history`, `/profile`) は `tests/integration/` 配下に任せる方針を維持。

## 残課題・次手の候補

1. **uv.lock 生成 + commit**
   - Dockerfile が `uv sync --frozen` を使うため、CI でビルドを通すには lock ファイルが必要
   - `uv sync` をローカルで実行して生成するフロー (私では実行できない)
2. **本番デプロイのドライラン**
   - `gcloud run deploy --source .` が成立するか要確認
   - Artifact Registry のリポジトリ作成手順も docs/operations.md に残す
3. **--live での Vertex AI ゴールデン評価**
   - 課金発生するためローカル/CI の手動実行限定
   - 初回 `--live` は 3〜5 ケースで pass 率を測定し、ベースラインを確立
4. **Firestore セキュリティルールのエミュレータ試験**
   - `firestore.rules` の挙動を Firebase Local Emulator Suite のテストランナーで検証する CI ジョブ
5. **観測性: `/metrics` エンドポイントまたは Cloud Logging → Monitoring の指標確認**
   - `guard_trigger_rate` 等がダッシュボードで見える状態にする

## 依存グラフの現状

```
domain ← guardrails ← services ← web/routes
         ↑                ↑
         repository ←─────┘
         (domain と dict のみに依存)
observability / config / llm は横断的に使用可
```

## 参照

- 計画: `~/.claude/plans/recipe-system-design-md-claude-md-curried-salamander.md`
- 前回: `memo/history/004-evaluation-and-devtools.md`

# 用語集

本プロジェクト内で使われる用語を固定する。LLM と人間の両方が読む前提で表記揺れを防ぐ。

## 五十音順

### アレルゲン

食物アレルギーの原因となる物質または食材。本システムでは消費者庁が定める特定原材料 28 品目を基盤に、家庭独自の追加項目を合わせた集合を指す。アレルゲンを含むレシピ提案は「ハードルール」で必ずブロックする。

### アレルゲングループ

アレルゲンを粒度の粗い単位でまとめたグループ名。例: `甲殻類`（エビ・カニ）、`ナッツ`（ピーナッツ・アーモンド・くるみ・カシューナッツ）。`allergens.yaml` の `groups.name` で定義される。

### ガードレール

LLM 出力を決定論的コードで検証し、ハードルール違反（アレルゲン等）を必ず弾く仕組み。本プロジェクトでは `src/recipe_system/guardrails/` に実装される。詳細は [guardrails.md](guardrails.md)。

### 強い選好

アレルギーほどではないが優先的に考慮すべき事項。嫌いな食材・直近 2〜4 週の履歴など。system prompt の冒頭に明示し、違反を `warn` として記録する。ハードルールと参考情報の間に位置する Layer 2。

### ゴールデンセット

プロンプト品質の回帰検証のための入力・期待値ペア集合。`evaluation/golden/*.jsonl` に格納する。期待値は「完全一致」ではなく「満たすべき性質」で記述する（例: `must_not_contain_allergen: [甲殻類]`）。詳細は [evaluation.md](evaluation.md)。

### 参考情報

LLM に context として渡すが、違反しても品質劣化にとどまる情報。レシピ集・過去の評価・在庫など。Layer 3 に位置する。

### 事後検証

LLM 出力をコードで再度チェックする三段階処理の最終段。アレルゲン検証・Pydantic スキーマ検証を行う。失敗時は最大 1 回リトライして、2 回目以降はフェイルオープンしない。

### 事前フィルタ

LLM に投入する前に Python コードで候補を絞り込む三段階処理の初段。アレルゲン含有レシピ除外・直近 7 日の履歴除外を行う。

### ハードルール

絶対に破ってはならないルール。本システムでは主にアレルギー情報。コードで弾き、LLM には任せない。Layer 1 に位置する。

### 正規化（canonical / alias）

食材名の表記揺れを統一する処理。LLM は「えび」「エビ」「海老」「シュリンプ」等を混在させるため、canonical 名（代表名）と alias（別名）のマッピングで吸収する。`src/recipe_system/guardrails/dictionaries/aliases.yaml` に定義。

### 未知食材

正規化辞書に未登録の食材。本システムのデフォルトポリシーは「警告ログに記録して提案は通す」。`block` にはしない。週次レビューで人間が判断し辞書に追加する。

## ABC 順

### ADR（Architecture Decision Records）

アーキテクチャ上の重要な決定を記録したドキュメント群。`docs/adr/` 配下に番号付きで格納する。「なぜその選択をしたか」を後から参照できるようにする目的。

### Cloud Run

Google Cloud のサーバレスコンテナ実行基盤。本プロジェクトでは `min-instances=0` で運用し、アイドル時のコストをゼロに抑える。

### Firestore Native Mode

Google Cloud のマネージド NoSQL データベース。本プロジェクトの主データストア。Native Mode（Datastore Mode ではなく）を採用する。

### Firebase Authentication

Google が提供する認証基盤。本プロジェクトでは家族メンバーの Google アカウント SSO に利用する。Identity Platform 無料枠（50,000 MAU）で家庭規模は完全に収まる。

### Phase 1 MVP

本プロジェクトの最小リリース範囲。夕食提案・家族プロファイル・履歴・アレルゲン検証・Prompt Caching まで。買い物リスト・在庫管理・Haiku 分担は Phase 2 以降に先送り。詳細は [architecture.md](architecture.md) §7。

### Prompt Caching

Anthropic API の機能で、リクエスト間で不変のプロンプト部分を Anthropic 側にキャッシュさせ、2 回目以降の課金を 90% 割引にする仕組み。本プロジェクトでは MVP から採用する。詳細は [llm-integration.md](llm-integration.md) §4。

### Vertex AI

Google Cloud の ML プラットフォーム。本プロジェクトでは Claude モデルを Vertex AI 経由で呼び出す（`anthropic[vertex]` SDK 利用）。

### 特定原材料 28 品目

消費者庁が食品表示法で定める特定原材料 8 品目（卵・乳・小麦・えび・かに・そば・落花生・くるみ）と、表示推奨される特定原材料に準ずるもの 20 品目を合わせた計 28 品目。本プロジェクトの食材正規化辞書のベースライン。

## 関連ドキュメント

- [architecture.md](architecture.md)
- [guardrails.md](guardrails.md)
- [llm-integration.md](llm-integration.md)
- [evaluation.md](evaluation.md)

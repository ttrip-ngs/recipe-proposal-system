# LLM 統合仕様

## 1. 前提

- Vertex AI 経由で Claude を利用する（`anthropic[vertex]` SDK）。これが主力実装であり、
  以降の記述は特記なき限り Claude を前提とする
- リージョン: `asia-northeast1`（東京）。検証環境では Claude のクォータ制約等により
  `global` エンドポイントを使う場合がある（`VERTEX_AI_LOCATION` で切替）
- 認証: サービスアカウントに `Vertex AI User` 権限を付与。ローカル開発では ADC（Application Default Credentials）を使用
- 学習への不使用: Vertex AI 経由の Claude 呼び出しは Anthropic 側の学習に使われない契約前提
- **LLM プロバイダ抽象化**: Claude のクォータ未承認・障害時などに実 LLM 応答を検証する
  代替経路として Gemini (Vertex AI 経由) や、api.anthropic.com を直接叩く経路にも
  切り替えられる。`LLM_PROVIDER` 環境変数 (`fake` / `claude` / `gemini` / `anthropic`。
  未指定時は `USE_FAKE_LLM` から後方互換的に導出) で選択する。詳細は §1.1、実装は
  `src/recipe_system/llm/client.py` の `build_client` / `build_raw_client` を参照。
  ガードレール (アレルゲン検証) はどのプロバイダでもコード側で行うため、この切替が
  ガードレールの安全性に影響することはない

### 1.1 プロバイダ切替 (LLM_PROVIDER)

| 環境変数 | 値 | 用途 |
|---|---|---|
| `LLM_PROVIDER` | `fake` | ローカル開発・単体テスト (固定レスポンス、課金なし) |
| `LLM_PROVIDER` | `claude` | 本番・通常運用 (Vertex AI 経由 Claude Sonnet 4.6 が既定) |
| `LLM_PROVIDER` | `gemini` | Claude クォータ未承認時などの代替経路 (Vertex AI 経由 Gemini 2.5 Flash が既定) |
| `LLM_PROVIDER` | `anthropic` | Vertex のモデルアクセス未承認時に api.anthropic.com を直接利用 (既定モデルは §2 の表を参照) |
| `LLM_MODEL` | 任意のモデル名 | アクティブなプロバイダのデフォルトモデルを上書き (例: `gemini-2.5-pro`) |
| `LLM_DETAIL_MODEL` | 任意のモデル名 | 週間提案の詳細フェーズ用モデルを上書き (§3.1 参照) |

`LLM_PROVIDER` 未指定の場合は既存の `USE_FAKE_LLM` (bool) から `fake`/`claude` を導出する
(`Settings.effective_llm_provider` 参照)。既存の `.env` / `.env.staging` はそのまま動作する。

Gemini は Claude と異なりプロンプトキャッシュを明示制御しない (§4 参照) ため、単価・
レイテンシ特性が異なる。品質検証目的の一時的な代替であり、恒久的な本番プロバイダは
Claude を想定する。

`anthropic` (api.anthropic.com 直接経路) は `ANTHROPIC_API_KEY` の設定が必須で、
Vertex 経由 (`claude`) とは課金経路が GCP から分離される (Anthropic Console 側の
請求になる)。Vertex 側のモデルアクセス審査が未承認の環境で、Claude をそのまま使いたい
場合の代替として用意する。ガードレール検証やプロンプト構造は Vertex 経由と同一。

## 2. モデル選定

### プロバイダ別の既定モデル (2026-09 現在)

| プロバイダ | 既定モデル | 備考 |
|---|---|---|
| `anthropic` (直接経路) | `claude-opus-5-5` | 現在の主力。Vertex が使えないため実運用はこちら |
| `claude` (Vertex 経由) | `claude-sonnet-4-6` | Vertex に Sonnet 5 の提供がないため据え置き |

`anthropic` の既定を Opus 5.5 にしたのは献立の質のため (memo/history/026)。同じ入力で
比較すると、Sonnet 5 (effort=low) は副菜・汁物が 7 日同じになる回があり、Haiku 4.5 は
食材リストの精度 (ひき肉でない豚肉の餃子、子どもに辛すぎる豆板醤の量など) が足りなかった。
単価は Opus 5.5 の方が高い (input 4 USD / output 20 USD per 1M) が、家庭の月数百円の範囲に収まる。

用途ごとの effort (`llm/factory.py` の `_EFFORT_BY_PURPOSE`):

| 用途 (`purpose`) | effort | 理由 |
|---|---|---|
| `single_day` / `weekly` (骨子・単日修復) | `medium` | 献立を決める呼出。旬・好物・週末の手間配分まで安定して反映された |
| `weekly_detail` (食材と手順) | `low` | 決まった料理の食材と手順を書くだけ。thinking なしで品質が保たれた |

Opus 5.5 は thinking を無効化できず、effort が推論量の唯一の調整手段 (max_tokens には
thinking も含まれる)。Haiku 4.5 は effort を受け付けないため、`LLM_DETAIL_MODEL` で
Haiku を指定した場合は送らない。アレルゲン検証はコード側で行うため、モデルや effort を
変えても安全性は変わらない。

Sonnet 5 は 2026-08-31 まで導入価格 (input 2 USD / output 10 USD per 1M) が適用されるが、
`llm/pricing.py` には 2026-09-01 以降の通常価格 (3 USD / 15 USD) を登録している。導入価格
期間中は実請求より過大に計上されるため、予算ガードは安全側に倒れる。

Claude 4.7 以降のモデル (Sonnet 5 を含む) は新しいトークナイザを使っており、同じ文章でも
Sonnet 4.6 以前より約 30% 多くトークンを消費する。単価の比較だけでコストを見積もらないこと。

### Phase 1 MVP

- Claude Sonnet 4.6（`claude-sonnet-4-6`）単体
- 夕食提案（主菜 1・副菜 1・汁物 1）を 1 回の呼び出しで生成
- 最大トークン数は 4000 (`suggest_dinner._SINGLE_DAY_MAX_TOKENS`)。3 品 + 食材リストで実測 1100-2200 トークンと幅があり、`LLMClient` の既定 2000 では途中で切れて JSON パースに失敗するケースが実際に発生した

### Phase B (週間提案、2026-05 追加。2026-09 削除)

- 同じ Sonnet 4.6 で 7 日分を 1 回で生成
- プロンプト: `suggest_weekly_dinner_system.yaml` / `suggest_weekly_dinner_user.yaml` (version: "1")
- 出力スキーマ: `LLMWeeklyProposal` (days: 7 × dishes: 3)
- トークン量は単日の約 7 倍を想定、Prompt Caching の system + family + candidates 部は単日提案と共通化してヒット率を上げる
- 最大トークン数は 16000 (`weekly_planner._WEEKLY_MAX_TOKENS`)。Sonnet 5 の実測で 1 回あたり 8000-9200 トークンを出力するため、8000 では 4-5 日目の途中で切れて JSON パースに失敗した。出力トークンは生成された分だけ課金されるので上限を大きく取ってもコストは増えない
- 1 回の生成に 80-110 秒かかる。ガードレール違反があると違反日の単日修復 (並列、1 日あたり 20-30 秒) が加算されるため、待機 UI は 3 分程度を想定すること
- Prompt Caching は実測で全く効いていない (`cache_read_tokens` / `cache_write_tokens` が 0)。system 単体が最小トークン下限に届かないこと、TTL 5 分に対し利用が 1 日 1 回であること、`previous_violations` / `recent_recipe_names` が system 側にあり呼び出しごとにプレフィックスが変わることが原因。コストの 96% は出力トークンであり、キャッシュを効かせても削減幅は 1 回あたり 1 円未満のため対応しない

### Phase 2 分割経路 (週間提案、2026-08 追加)

Phase B の「1 回で 7 日分」を、骨子と日別の食材詳細 (並列) の 2 段に分割した。
2026-09 に分割経路の実測コストが 1 回呼び出しを下回ったため、1 回呼び出しの経路
(`suggest_weekly_dinner_*.yaml` / `WEEKLY_SPLIT_ENABLED`) は削除した。詳細は §3.1 を参照。

### Phase 4

- Opus 4.7 による週間献立プランニング（戦略レイヤー、週 1 回バッチ）

### 代替プロバイダ (Gemini)

Claude のクォータ未承認時などの検証用に Gemini 2.5 Flash (`gemini-2.5-flash`) を
既定モデルとして用意する。`LLM_MODEL=gemini-2.5-pro` で高品質モデルに切替可能。
本番の主力モデルではなく、§1.1 の代替経路として位置づける。

## 3. プロンプト全体構造

```
[system]
  ├── ルール（🚨 絶対禁止・推奨・参考の階層明示）
  ├── 責務定義（食材の正規名で返す、在庫優先 等）
  └── 出力 JSON スキーマの提示
[user]
  ├── 家族プロファイル（アレルゲン・嫌い・好き）
  ├── 候補レシピ集（事前フィルタ済み）
  ├── 直近履歴サマリ（カテゴリ別集計済み）
  ├── 現在の在庫
  └── 今日の要望
```

MVP では system と user の境界を明確にし、ユーザー起源の自由記述（メモ等）は `<user_note>...</user_note>` タグで囲んでプロンプトインジェクション経路を限定する。

### 3.1 週間提案の分割経路 (骨子 + 日別詳細)

週間提案は 2 段構成をとる。

```
[骨子 1 回 / Sonnet]  plan_weekly_skeleton_system.yaml + _user.yaml
    7 日 × 3 品の name / category / main_ingredient / reason (食材リストなし)
        |
        v
[詳細 7 並列 / effort=low] detail_day_ingredients_system.yaml + _user.yaml
    1 日分の 3 品それぞれの食材リストと作り方 (asyncio.gather)
        |
        v
[マージ] index で突き合わせ -> [事後検証 (コード, 各日)] -> [違反日のみ単日修復]
```

**なぜ骨子で食材を出させないか**: コストの 96% とレイテンシのほぼ全ては出力トークンで
決まる。21 品分の食材リストを Sonnet に書かせると分割の意味がなくなるため、骨子は
「何を作るか」の決定だけに絞り、`reason` も 30 字以内に制限する。

**マージは `index`、料理名は warn のみ**: 実 LLM は「豚の生姜焼き」を「豚肉の生姜焼き」の
ように言い換えることがある。マージのずれは安全性に影響しない (`collect_violations` は
日内の全食材を検証するため) ので、名前の不一致は `weekly.detail.name_mismatch` の warn に
留める。ここで block にすると、安全性に無関係な理由で高価な単日修復にエスカレーション
してしまう。block にするのは品数不一致・index 欠落・パース失敗 (1 回再試行後) のみ。

**全日失敗時は週全体を error にする**: 確定できる日が 1 つも無い状態で 7 日分の単日修復に
流すと、コストもレイテンシも大きく悪化する。`WeeklyDetailAllFailedError` で
打ち切る (フェイルオープンはしない)。

**モデル解決**: 詳細フェーズは既定で骨子と同じモデル (`resolve_detail_model` は
`LLM_DETAIL_MODEL` の上書きのみ返す)。effort は用途別に変える (§2)。コスト按分は
`llm_usage` の `purpose="weekly"` (骨子) と `purpose="weekly_detail"` (詳細) で確認する。

**作り方 (steps)**: 詳細フェーズは食材リストと同じ呼出で 1 品 3-5 ステップの作り方を
生成する。別呼出にしないのは、手順にだけ食材リストに無い食材が現れるのを防ぐため。
それでも手順にアレルゲンが書かれる可能性は残るので、手順の文章もガードレールが
アレルゲンの語で検査する (docs/guardrails.md)。

**実測 (2026-09-26, anthropic プロバイダ, 検証環境)**:

| 構成 | 時間 | コスト | 備考 |
|---|---|---|---|
| 1 回呼び出し (Sonnet 5) | 約 81 秒 | 21.2 円 | 削除済み |
| 分割 Sonnet 5 骨子 + Haiku 詳細 (構造化出力・effort 導入前) | 約 94 秒 | 約 30 円 | memo/history/023 |
| 同 (構造化出力 + Sonnet effort=low) | 32.5 秒 | 16.8 円 | memo/history/023 |
| 分割 Opus 5.5 骨子 medium + 詳細 low、作り方付き (現行) | 48.7 秒 | 51.4 円 | memo/history/026 |

コストは `llm/pricing.py` ベース (違反日の単日修復を含む)。現行構成の内訳は骨子 13.5 円、
詳細 7 回で約 38 円。

## 4. Prompt Caching 設計

Prompt Caching を MVP で採用する。breakpoint 位置とキャッシュ対象を以下とする。

```
[system]                           <- cache breakpoint 1（不変）
  ルール・責務・出力スキーマ
[user]
  家族プロファイル                  <- cache breakpoint 2（月次更新）
  候補レシピ集                      <- cache breakpoint 3（レシピ追加時のみ変動）
  ----------------------------------
  直近履歴サマリ                    <- ここからキャッシュしない
  在庫
  今日の要望
```

- TTL: デフォルト 5 分、必要なら 1 時間拡張オプションを検討
- Cache write: 通常 input の 1.25 倍（初回ペナルティ）
- Cache read: 通常 input の 0.1 倍（90% 割引）
- breakpoint は最大 4 つまで使用可能

実装は `anthropic[vertex]` SDK の `cache_control={"type": "ephemeral"}` を該当ブロックに付与する。

**Gemini 使用時の扱い**: Gemini 2.5 系は implicit caching (自動キャッシュ) を採用しており、
Claude のような breakpoint 明示指定はできない。そのため `VertexGeminiClient` では
`USE_PROMPT_CACHE` は no-op であり、上記の breakpoint 設計は Claude 経路にのみ適用される。

## 5. プロンプト YAML スキーマ

`src/recipe_system/llm/prompts/` 配下に 1 プロンプト 1 ファイルで配置する。

```yaml
name: suggest_dinner_system
version: "3"
model: claude-sonnet-4-6
description: 夕食献立提案の system プロンプト
variables:
  - family_profile
  - rules
template: |
  あなたは家族 {{ family_profile.family_name }} の管理栄養士です。
  ...
  # 絶対禁止
  {% for member in family_profile.members %}
  - {{ member.name }}: {{ member.allergens | join(", ") }}
  {% endfor %}
  ...
```

- `version` フィールド必須。プロンプト変更時はインクリメントする
- Jinja2 テンプレートで変数展開する
- 複数プロンプト（system / user / retry_instruction など）は別ファイルに分離する

## 6. 出力 JSON スキーマ

応答の JSON は `LLMClient.generate` の `output_schema` に Pydantic モデルを渡し、
対応プロバイダでは API 側の構造化出力 (制約付きデコード) で生成させる。

| プロバイダ | 仕組み |
|---|---|
| `anthropic` / `claude` (Vertex) | `output_config.format` (`anthropic.transform_schema` で変換) |
| `gemini` | `response_schema` |
| `fake` | 無視 (固定応答) |

導入の契機は、実 Haiku 4.5 が `"quantity": 大さじ` のように数値欄へ引用符なしの文字列を
書き、同じ誤りを再試行でも繰り返して単日修復 (Sonnet) に流れていたこと。プロンプトで
「quantity は数値」と指示済みでも防げなかったため、プロンプトではなくデコード側で
不正な JSON を生成できなくした。`ge` / `max_length` などスキーマ変換で落ちる制約は
呼出側の `parse_llm_json` が Pydantic で再検証するため、安全性の担保は従来と変わらない。

### 6.1 単日提案 (suggest_dinner_*)

```json
{
  "dishes": [
    {
      "name": "肉じゃが",
      "category": "主菜",
      "main_ingredient": "牛肉",
      "reason": "直近に肉料理が少なく在庫の牛肉を活用できるため",
      "ingredients": [
        {"name": "牛肉", "quantity": 300, "unit": "g"},
        {"name": "じゃがいも", "quantity": 4, "unit": "個"}
      ]
    }
  ],
  "overall_comment": "和食中心で栄養バランスを意識しました"
}
```

### 6.2 週間提案 (骨子 + 日別詳細)

骨子 (`LLMWeeklySkeleton`) は 7 日 x 3 品の name / category / main_ingredient / reason
のみ。日別詳細 (`LLMDayDetail`) は骨子の index ごとに食材リストと作り方を返す。

```json
{
  "dishes": [
    {
      "index": 0,
      "name": "豚の生姜焼き",
      "ingredients": [{"name": "豚肉", "quantity": 400, "unit": "g"}],
      "steps": ["玉ねぎを薄切りにする", "豚肉を焼き、たれを絡める"]
    }
  ]
}
```

単日提案 (`LLMProposal`) の各 dish も `steps` を持つ (単日修復の結果も週間の画面に並ぶため)。
Pydantic モデルは `src/recipe_system/domain/llm_output.py` に定義する。JSON 整形に失敗した
場合は 1 回だけ再要求し、2 回目は失敗扱いとする。

週間提案のガードレール検証は各日の dishes に対して個別に行う。block 違反のある日のみ `suggest_dinner` (単日提案) で作り直し (最大 1 回、複数日は並列)、リトライ後も残ればその日だけ `status=empty` で返し、他の日は確定する (週全体フェイルにはしない)。修復呼び出しが例外で終わった日も同様にその日だけ空にする。

修復では `suggest_dinner` が内部でさらに最大 1 回のガードレールリトライを行うため、違反日は「週間生成 (骨子 + 詳細) -> 単日修復 -> 単日修復内のリトライ」で最大 2 回の再依頼を受ける。CLAUDE.md 6.7 の「LLM 再依頼は最大 1 回」は 1 つのユースケース内での上限を指し、週間提案では「違反日あたり単日修復 1 回 (その内部の単日既定リトライ 1 回を含む) を上限」と定める。回数は有限で、自動的なルール緩和もフェイルオープンもしない。

修復対象日には、週内の他の日で採用済みの料理名と違反日自身の 1 回目の料理名を `recent_history` に注入し、事前フィルタで除外する。これにより週内重複と違反した料理そのものの再提案を防ぐ。

## 7. エラー処理

- Vertex AI レート制限（429）: 指数バックオフで最大 3 回リトライ
- タイムアウト: 30 秒で打ち切り、UI に「通信中です」を表示
- JSON パース失敗 (JSON デコード失敗・スキーマ検証失敗): 同一プロンプトで 1 回だけ
  再要求する (`services/suggest_dinner.py` / `services/weekly_planner.py` の
  `_attempt_with_parse_retry`)。ガードレール違反によるリトライとは独立した
  仕組みで、2 回目も失敗すれば `LLMResponseParseError` を呼出元に伝播する
  (フェイルオープンしない)。実 Gemini 検証で数十回に 1 回程度、稀に不正な
  JSON が返るケースがあり導入した (memo/history/018 参照)
- Vertex AI 側障害: Cloud Logging に error 記録し、UI でフォールバック画面を表示（手動選択を促す）

## 8. 構造化ログ必須項目

すべての LLM 呼び出しで以下を構造化ログに記録する。

| フィールド | 型 | 説明 |
|---|---|---|
| request_id | string | リクエスト ID |
| family_id | string | 呼び出し元家族 |
| prompt_version | string | プロンプト YAML の version |
| model | string | 使用モデル |
| input_tokens | integer | |
| output_tokens | integer | |
| cache_read_tokens | integer | Prompt Caching 利用時 |
| cache_write_tokens | integer | 初回書き込み時 |
| guard_violations_count | integer | ガード違反件数 |
| guard_severity | string | `none` / `warn` / `block` |
| retry_count | integer | 0 または 1 |
| latency_ms | integer | 呼び出しレイテンシ |

これらは Cloud Logging のログベース指標で `guard_trigger_rate` 等の計測に使う（[operations.md](operations.md) 参照）。

## 9. フェイク LLM モード

ローカル開発・テスト用に `LLM_PROVIDER=fake` (または後方互換の `USE_FAKE_LLM=true`) 時は固定レスポンスを返すフェイククライアントを使う。`src/recipe_system/llm/client.py` に `FakeVertexClient` を実装し、`evaluation/golden/` 配下のサンプルから決定論的に応答を組み立てる。Vertex AI 課金なしで UI 開発・テストが可能。

## 10. 予算ガード (BudgetGuardedClient)

実 LLM 切替時の運用安全装置として、`build_client(*, purpose, family_id)` は
常に `BudgetGuardedClient` でラップした実装を返す。

- `purpose`: `"single_day" | "weekly" | "other"` のいずれか。`llm_usage` の
  集計分類と将来的なレート制限の単位に使う。
- 呼出直前に Firestore の `config/llm_budget.monthly_jpy_limit` と
  `llm_usage` の月次累積を取得し、累積 ≥ 上限なら API を呼ばずに
  `BudgetExceededError` を投げる（block-to-safe）。
- Vertex AI 呼出後（成功・失敗どちらでも）、`llm_usage` Firestore コレク
  ションに 1 件記録。Fake モードはコスト 0 として記録される。
- 料金表は `src/recipe_system/llm/pricing.py` の `PRICING_TABLE` に集約。
  Anthropic / Google 公式の USD 単価 (Claude Sonnet 4.6 に加え、代替経路の
  Gemini 2.5 Flash / Pro も登録) を保持し、`USD_JPY_RATE` 環境変数（デフォルト
  155.0）で JPY 換算する。価格表に未登録のモデルはコスト 0 円として記録される
  上、`fake-sonnet` / `unknown` 以外は警告ログ (`pricing.unknown_model`) を
  出す (価格表への追加漏れ、すなわち課金の過小計上に気づけるようにするため)。
- UI 連携: `web/routes/plans.py` の BackgroundTask が
  `BudgetExceededError` を捕捉し、提案ドキュメントの `error_code` を
  `"budget_exceeded"` に設定。提案画面 (`plans/day.html`) で専用メッセー
  ジを表示する。

詳細運用は [operations.md §8.3](operations.md) を参照。

## 11. 関連ドキュメント

- [guardrails.md](guardrails.md) - LLM 出力のガードレール検証
- [evaluation.md](evaluation.md) - プロンプト版管理・ゴールデンセット
- [operations.md](operations.md) - コスト見積・ログ設計
- [adr/0005-pluggable-llm-provider.md](adr/0005-pluggable-llm-provider.md) - プロバイダ抽象化の設計判断

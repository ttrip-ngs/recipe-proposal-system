# 020 Anthropic API 直接経路への移行と、実 LLM で発覚した不具合の修正

## 背景

Vertex AI 経由で Claude Sonnet 4.6 を使う検証環境 (016 参照) は、クォータ 0 の
ままブロックされていた。Model Garden 上でモデルを有効化したので通るはず、という
想定で再検証したところ、状況は変わっていなかった。

調査の結果、以下が判明した。

- モデルアクセス自体は通っている。global / us-east5 / europe-west1 / europe-west4 /
  asia-southeast1 のいずれも 429 (Quota exceeded) を返す。404 ではないので、
  提供はされているが割当が 0 という状態
- Anthropic 系は sonnet-4-5 / haiku-4-5 / opus-4-1 も同様に 0。プロジェクト
  `<GCP_PROJECT_ID>` 全体の事象で、4.6 固有ではない
- Cloud Quotas API から増加申請 (`quotaIncreaseEligibility.isEligible: true`) を
  3 件出したが、いずれも即時 "Quota request denied"。2026-07-04 に Console から
  出していた申請も却下済み

これは Vertex AI 上の Claude で広く報告されている既知事象で、実体はクォータでは
なくモデルアクセス適格性の審査 (請求アカウントの成熟度・トライアル外の支出実績・
Vertex 利用実績・内部信頼スコア) である。セルフサービスのクォータ申請は自動却下
され続けるため、再申請しても意味がない。

## 対応方針

api.anthropic.com を直接叩く経路を新プロバイダとして追加し、検証・実運用を
そちらに寄せた。Vertex 経由 (`LLM_PROVIDER=claude`) は本番想定として温存する。

比較検討では OpenAI も候補に挙がったが、既存の `anthropic` SDK と
`llm/claude.py` をほぼ流用でき、`docs/llm-integration.md` の Prompt Caching 設計や
Phase 2 以降のモデル分担設計 (Sonnet + Haiku) の前提も崩れないため、Anthropic
直接を選択した。

## 変更内容

### 1. Anthropic 直接プロバイダの追加

- `llm/anthropic_api.py` に `AnthropicDirectClient` を追加 (`LLM_PROVIDER=anthropic`)
- Vertex 経由と重複していた system payload 組み立て・レスポンス変換を
  `llm/anthropic_common.py` に集約。`llm/claude.py` は挙動不変のまま整理した
- `config.py` に `ANTHROPIC_API_KEY` を追加。依存追加は不要
  (`anthropic[vertex]` に基本パッケージが含まれる)

### 2. モデルを Sonnet 5 / Haiku 4.5 へ

直接経路の既定モデルを `claude-sonnet-5` にした。Vertex 経由は Sonnet 5 の提供が
ないため 4.6 のまま据え置き。`LLM_MODEL=claude-haiku-4-5` で軽量経路に切替可能。

`pricing.py` には Sonnet 5 の 2026-09-01 以降の通常価格 (3/15 USD) を登録した。
2026-08-31 までの導入価格 (2/10 USD) より高く計上されるため、予算ガードは安全側に
倒れる。導入価格を登録して後で上げる設計だと「上げ忘れ = 過少計上 = 予算ガード
素通り」という危険な失効モードがあるため、この向きを選んだ。

Haiku はエイリアス指定でも API が日付付き ID (`claude-haiku-4-5-20251001`) を返す
ため、`_normalize_model` で日付サフィックスを落として単価を引く。

### 3. 実 LLM を通して初めて発覚した不具合 3 件

いずれも Fake LLM とゴールデンセットは整形済み JSON を返すため、実 API を叩くまで
表面化しなかった。

**JSON のコードフェンス**: Sonnet 5 / Haiku 4.5 とも、system プロンプトで JSON
スキーマのみを提示しても応答を ```json のフェンスで包んで返す。`parse_llm_json` が
`json.loads(raw)` を直接呼んでいたため、全提案がパース失敗していた。Gemini は
`response_mime_type=application/json` で回避していたが Anthropic 系に同等の指定が
ないため、プロバイダ非依存の後処理としてフェンス除去を追加した。

**週間提案の max_tokens 不足**: `max_tokens` を渡しておらず既定 2000 のままで、
出力が途中で切れて壊れた JSON になっていた。Sonnet 5 は 7 日分 21 品の食材リストに
8,800-11,600 トークンを使う。docs の目安 5000-6000 でも足りず、16000 に設定した。
切り詰めが即座に分かるよう、`stop_reason == "max_tokens"` の警告ログも追加した。

**単日提案の max_tokens 不足**: 同じ問題が単日側にもあった。実測 1,100-2,200
トークンと幅があり既定 2000 では足りない。4000 を明示指定した。

### 4. 週間提案のガードレールリトライを違反日限定に

block 違反が 1 日でも出ると週全体 (約 9,000 トークン / 85 秒) を再生成していた。
これを違反日のみ `suggest_dinner` で並列に作り直す方式に変更した。

この変更は正しさの修正も兼ねている。

- 従来はリトライ結果で週全体を置き換えていたため、1 回目に成功していた日まで
  2 回目の生成物で上書きされ、本来確定できた提案を捨てる可能性があった
  (モジュール docstring の記述とも乖離していた)
- `llm_meta` が最後の attempt のメタしか保存しておらず、コストが最大半分に
  過少計上されていた。全呼び出しの合計を記録するようにした

`asyncio.gather` は `return_exceptions=True` が必須。修復 1 日のパース失敗や予算
超過で例外が伝播すると、週間生成が成功していた他の日まで巻き込まれ、「その日だけ
status=empty で返す」という前提が崩れる。

### 5. 待機 UI をポーリング方式に

生成待ち画面は `<meta http-equiv="refresh" content="3; ...">` でページ全体を 3 秒ごと
に再読み込みしており、ちらつき・スクロール位置のリセットが起きていた。週間提案は
3 分近くかかるため 60 回近い全画面再描画になる。

`{"done": bool}` だけを返すステータス JSON を 3 本追加し、JS の fetch ポーリングで
完了時に 1 回だけリロードする方式に変えた。

あわせて `/static` のキャッシュバスティングを追加した。ブラウザが古い `app.js` を
再検証なしに使い続け、ポーリングが動かない事象が実際に起きたため
(meta refresh を消した後だったので画面が完全に止まって見えた)。
`static_url()` がファイルの mtime をクエリに付ける。

## コスト・レイテンシの実測 (Sonnet 5、155 円/USD)

| | 入力 | 出力 | 所要 | コスト |
|---|---|---|---|---|
| 単日提案 | 1,543 | 1,121 | 13 秒 | 3.3 円 |
| 単日 x 7 | 10,801 | 7,847 | 92 秒 | 23.3 円 |
| 週間提案 (違反なし) | 1,674 | 8,776 | 81 秒 | 21.2 円 |
| 週間提案 (違反 2 日 + 修復) | 5,540 | 17,900 | 156 秒 | 44 円 |

コストの 96% は出力トークンで、レイテンシもほぼ出力生成時間 (約 108 tok/s)。
入力側の最適化は効果が薄い。

Prompt Caching は実測で全く効いていない (`cache_read_tokens` / `cache_write_tokens`
が 0)。system 単体が最小トークン下限に届かないこと、TTL 5 分に対し利用が 1 日 1 回
であること、`previous_violations` / `recent_recipe_names` が system 側にあり呼び出し
ごとにプレフィックスが変わることが原因。効かせても削減は 1 回あたり 1 円未満のため
対応しない方針とした。

## 検証環境の注意点

`ENV_FILE=.env.staging` を付けるだけでは `FIRESTORE_EMULATOR_HOST` がプロセスの
環境変数に入らず、Firestore SDK が本番を掴む (pydantic-settings が読むのは Settings
オブジェクトだけで、SDK は `os.environ` を見るため)。`set -a; source .env.staging;
set +a` で export してから起動する必要がある。

また Auth エミュレータが `recipe-system-dev` プロジェクトで動いている場合、
`.env.staging` の `GOOGLE_CLOUD_PROJECT` (Vertex 用に `<GCP_PROJECT_ID>`)
と食い違い、ID トークンの `aud` 不一致で全ログインが 401 になる。Anthropic 直接経路
では Vertex のプロジェクト ID は不要なので、`GOOGLE_CLOUD_PROJECT` はエミュレータ側
に合わせるのが正しい。

## 残課題

- ガードレール違反率が生成ごとにばらつく (7 日中 0-2 日)。週間生成は 1 回で 21 品を
  出すため単日の 7 倍の暴露量があり、長い生成の後半ほど禁止則の遵守が緩む可能性が
  ある。数週分の `violations` を見て、真の混入か正規化辞書の当たりすぎかを分類する
- `llm/prompts/*.yaml` の `model:` フィールドが実態とずれている (未使用のデッド
  メタデータ)。`suggest_weekly_dinner_system.yaml` の `previous_violations` ブロックも
  単日修復への変更でデッドコード化した。いずれも CLAUDE.md 6.3 のフロー
  (version インクリメント + ゴールデンセット評価) に従って別途整理する
- Phase 2 (Sonnet で週骨子 + Haiku 4.5 で日別詳細を並列生成) の前倒し。試算では
  週間提案が 21.2 円 / 81 秒 から 10.7 円 / 20 秒 になる
- Vertex のモデルアクセスは、Google Cloud サポートへのケース起票 (Standard サポート
  契約が必要) かアカウントマネージャー経由でのみ開通しうる

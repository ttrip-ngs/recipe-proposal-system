# 021 週間提案を骨子 (Sonnet) + 日別詳細 (Haiku 並列) に分割

2026-08-01

## 背景

020 の残課題に挙げていた Phase 2 (Sonnet で週骨子 + Haiku 4.5 で日別詳細を並列生成) を
実装した。従来の週間提案は Sonnet 5 に 1 回の呼び出しで 7 日 × 3 品 (食材リスト込み) を
生成させており、実測で 80-110 秒 / 約 21.2 円かかっていた。コストの 96% とレイテンシの
ほぼ全ては出力トークンで決まるため、出力の大半を占める食材リストを軽量モデルに並列で
書かせるのが狙い。

## 実装

### 1. 2 段構成

```
[骨子 1 回 / Sonnet]  plan_weekly_skeleton_*.yaml   max_tokens=6000
    7 日 × 3 品の name / category / main_ingredient / reason (食材リストなし)
[詳細 7 並列 / Haiku] detail_day_ingredients_*.yaml max_tokens=2000
    1 日分 3 品の食材リスト (asyncio.gather)
[マージ] index で突き合わせ -> 事後検証 -> 違反日のみ単日修復 (従来どおり)
```

骨子の `reason` は 30 字以内に制限した。骨子の出力サイズが全体の支配項になるため。

### 2. マージは index、料理名は warn 止まり

当初は料理名で突き合わせる設計にしていたが、設計レビューで否定された。実 LLM は
「豚の生姜焼き」を「豚肉の生姜焼き」のように言い換えるため、名前一致方式では表記揺れが
そのまま block になり、Sonnet 単日修復 (約 3.3 円 / 13 秒) へのエスカレーションが多発して
分割の削減分を食い潰す。

重要なのは、**マージの取り違えは安全性に影響しない**という点。`collect_violations` は
日内の全 dish の全食材を `validate_recipe` にかけるため、食材がどの料理に紐付いたかに
関係なくアレルゲンは検出される。したがって名前不一致は UX 上の紐付けミスでしかなく、
`weekly.detail.name_mismatch` の warn に留めた。

block にするのは以下のみ (いずれもフェイルクローズ)。

- 詳細の品数が骨子と一致しない
- 骨子の index が詳細応答に存在しない
- JSON パース / スキーマ検証の失敗 (`attempt_with_parse_retry` で 1 回再試行後)

`LLMDishIngredients.ingredients` には `min_length=1` を付けた。食材リストが空だと
ガードレールの検証対象が無くなり「何も検証されずに通る」ため、スキーマ段階で弾く。

### 3. 全日失敗時は週全体を error

詳細が 7 日すべて失敗した場合 (プロンプト不備・レート制限など系統的な失敗) に、
そのまま 7 日分の単日修復に流すと骨子 10 円 + 修復 23 円でレガシー経路より高く遅くなる。
確定できる日が 1 つも無いので「他の日は確定させる」原則とも矛盾しないため、
`WeeklyDetailAllFailedError` で打ち切る。1 日でも成功していれば従来どおり失敗日だけ
単日修復に回す。

### 4. モデル解決とコスト按分

`llm/factory.py` に `resolve_detail_model` / `build_detail_client` を追加した。
プロバイダ別テーブルで anthropic のみ `claude-haiku-4-5` を割り当て、Vertex 経由
(`claude`) と `gemini` は既定モデルのままにしている。Vertex は Claude のクォータが
未承認で使えないため、使える状態になってから割り当てる (YAGNI)。

`UsagePurpose` に `weekly_detail` を追加し、`llm_usage` で骨子と詳細の按分が
`/admin/usage` から見えるようにした。`proposals` レコードの `model` は代表値 1 つしか
持てないため、モデル別の内訳は `llm_usage` 側で確認する。

### 5. 切り戻しフラグと評価経路

`WEEKLY_SPLIT_ENABLED` (既定 true) で従来の 1 回呼び出し経路に戻せる。`plan_week` 自体は
config を読まず「`detail_llm` が渡されたら分割」という契約にして、フラグの解釈は
呼出元 (`proposal_workflow` / `evaluation/runner`) に置いた。

`evaluation/runner.py` の `_evaluate_weekly_case` も同じフラグに従って分割経路を通すように
した。ここを対応しないとゴールデンセット評価が永久にレガシー経路しか通らず、新規プロンプト
4 本の退行を CI で検出できない (CLAUDE.md 6.3 が形骸化する)。

### 6. スキーマ段階での一意性検証

`LLMWeeklySkeletonDay` に日内 index の一意性、`LLMWeeklySkeleton` に day_offset の
一意性の `model_validator` を追加した。index が重複するとマージ用の辞書が後勝ちになり、
2 品が同じ食材リストを持つ。安全性には影響しない (全食材が検証される) が、品質バグが
黙って通るため、パース失敗として再試行・単日修復の既存経路に乗せる。

## 副次的に直したもの

### evaluation/runner.py の sys.path

`REPO_ROOT` を計算しているのに `sys.path` へ入れておらず、docs 記載の
`uv run python evaluation/runner.py` が `ModuleNotFoundError: No module named 'evaluation'`
で起動できなかった。`sys.path` に `REPO_ROOT` を追加して修正した。

### llm_usage の purpose 白リスト

`llm_usage_repository._to_record` が読み出し時の purpose 白リストを
`("single_day", "weekly", "other")` と直書きしており、`weekly_detail` を Literal に
足しただけでは、正しく書き込まれた詳細フェーズの記録が読み出しで静かに `other` へ
落ちていた (レビュー指摘で発覚)。白リストを `get_args(UsagePurpose)` から導出する形に
変え、`/admin/usage` のテンプレートにも `weekly_detail` の行を追加した。purpose を
分けた目的そのものが初日から成立しなくなるところだった。

## 検証結果

- `uv run pytest`: 221 件パス (分割経路まわりのテスト 18 件を追加)
- ゴールデンセット評価: 分割経路・レガシー経路とも 11 件中 10 件パスで**完全に一致**。
  唯一の失敗 G101 は単日提案のケースで、Fake LLM が常に肉じゃがを返すことによる
  分割前からの既知の失敗 (本変更とは無関係)
- `uv run ruff check` / `uv run mypy src`: クリーン

## 未検証 / 残課題

- **実 LLM でのコスト・レイテンシは未実測**。設計レビュー時の試算では骨子の出力が
  3000-4500 トークンになる見込みで、約 15 円 / 45 秒 (現行 21.2 円 / 81 秒)。020 の
  試算値 10.7 円 / 20 秒 に届くのは骨子出力が 2000 トークン程度に収まる場合のみ。
  検証環境で実測して数値をこのメモに追記すること
  -> 2026-09-26 に実測。thinking と Haiku の JSON 不正で約 94 秒 / 約 30 円だったため、
  構造化出力と Sonnet 5 の effort=low を入れて 32.5 秒 / 16.8 円にした (023 参照)
- **レガシー経路の撤去基準**: 検証環境で数週分の実運用を回し、ゴールデンセット評価が
  分割経路で同等以上を維持し、実測コストが現行を下回ることを確認できたら、
  `suggest_weekly_dinner_*.yaml` と `_generate_single_call` / `WEEKLY_SPLIT_ENABLED` を
  削除する。無期限併存にはしない
- 詳細フェーズの 7 並列は各自が予算 preflight を通るため、上限際で最大 7 呼出分
  (数円) の超過がありうる。既存の並列修復と同じ性質のため許容している
- `llm/prompts/*.yaml` の `model:` フィールドが実態とずれている件 (020 の残課題) は
  未着手。新規 4 本には最初から書いていない

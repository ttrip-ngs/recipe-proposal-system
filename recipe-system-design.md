# 家庭内レシピ提案システム 設計ドキュメント

## 1. プロジェクト概要

家庭内のレシピ提案・買い出し管理を行うシステム。LLM（Vertex AI経由）を活用し、過去のレシピ履歴・家族の好き嫌い・アレルギー情報を蓄積して継続的に提案品質を向上させる。

### 要件

- レシピ提案と買い物リスト生成をLLMで実施
- 家族情報（好き嫌い・アレルギー）を蓄積・活用
- 過去のレシピ履歴を蓄積し、月内で同じレシピに偏らない
- **アレルギー・好き嫌いは絶対に考慮漏れを無くしたい**

---

## 2. データ持ち方の方針: RAG vs テキスト/MD直投げ

### 検討した論点

- RAGにするか、Markdown/テキスト形式でプロンプトに直接埋め込むか

### 結論: まずはMD/テキスト直投げで開始、スケールしたらRAG化

### 理由

**データ量が小さい**

家庭用データは以下の規模感で、Geminiの1M contextや Claude Sonnet 4.6 / Opus 4.7 の200k contextに余裕で収まる:

- 家族プロファイル: 数KB
- レシピ履歴（年間300〜500食）: 数百KB
- 定番レシピ集（100品）: 数百KB

**RAGはオーバーキル**

- ベクトルDB運用、embedding更新、チャンク戦略、検索品質チューニングの工数が家庭用には重い

**ホリスティック判断が必要**

「最近鶏肉続いてるから魚にしよう」「先週と被らないように」のような判断は、RAGの類似検索で上位k件取得する方式だと取りこぼすリスクがある。全履歴を読ませた方が品質が上がる。

### 推奨ファイル構造

```
data/
├── family.md           # 家族構成・アレルギー・好き嫌い・栄養方針
├── recipes/
│   ├── 定番.md         # よく作るレシピ集
│   └── お気に入り.md
├── history/
│   ├── 2026-04.md      # 月別履歴（作った日・評価・メモ）
│   └── 2026-03.md
└── pantry.md           # 現在の在庫・調味料
```

- Git管理で差分追跡
- 既存ワークフロー（chezmoi, Neovim, WezTerm）と親和性高
- LLMに渡すときは全部concatしてプロンプトに投入

### RAG化を検討するライン

- 履歴が数MB超え（5年以上運用＋レシピ本スキャン取り込み等）
- 画像レシピを大量に扱う場合
- 複数家庭で共有して他家庭のレシピも検索したい場合

→ この段階でVertex AI Search を被せる

---

## 3. 重要度によるレイヤー分け設計

### 課題

全部をいっぺんにLLMに読ませると「読んだけど見落とす」リスク。特にアレルギーは絶対外せない。

### 解決: 3レイヤー構造

```
┌─────────────────────────────────────┐
│ Layer 1: ハードルール（絶対）       │
│ - アレルギー情報                    │
│ - 医療上のNG食材                    │
│ → コードで弾く、LLMに任せない       │
├─────────────────────────────────────┤
│ Layer 2: 強い選好（system prompt）  │
│ - 嫌いな食材                        │
│ - 最近の献立履歴（直近2-4週）       │
│ → system promptの冒頭に明記         │
├─────────────────────────────────────┤
│ Layer 3: 参考情報（context）        │
│ - レシピ集                          │
│ - 過去の評価                        │
│ - 在庫                              │
└─────────────────────────────────────┘
```

### 設計思想

**LLMに何を任せて何を任せないかの境界設計が本質**

| 責務 | 担当 |
|------|------|
| 賢い提案（バリエーション、季節感、栄養バランス） | LLM |
| ハードな禁止ルール | コード |
| 履歴の集計・統計 | コード |
| レシピ本文の自然言語解釈 | LLM |
| 食材の正規化・タグ付け | コード（初期はLLM補助） |
| 最終承認 | コード |

LLM は **creative** な部分、コードは **correct** な部分を担当。命に関わるルール（アレルギー）は確率的なものに任せない。

---

## 4. ガードレール: 決定論的な検証

### 方針: LLMの出力をLLMを使わずに機械的に検証

LLMに「アレルゲン入ってない？」と聞く方式がダメな理由:

- 確率的に動くので100%保証ができない
- プロンプトインジェクション耐性がない
- コストがかかる（毎回API呼び出し）
- 遅い（数秒のレイテンシ）
- テストが書きにくい

対してコード検証は:

- 決定論的: 同じ入力 → 必ず同じ出力
- ユニットテスト可能
- ミリ秒で完了
- 監査可能

インフラ的感覚で言えば「ACLとファイアウォール」。LLMはサジェスタ、denyは決定論的ルールエンジン。

### 実装イメージ

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class Ingredient:
    name: str          # "むきえび"
    normalized: str    # "エビ"
    allergen_tags: set[str]  # {"甲殻類", "エビ"}

@dataclass
class FamilyMember:
    name: str
    allergens: set[str]
    dislikes: set[str]

@dataclass
class Violation:
    severity: Literal["block", "warn"]
    member: str
    ingredient: str
    reason: str

def validate_recipe(
    recipe: Recipe,
    family: list[FamilyMember],
) -> list[Violation]:
    violations = []
    for ing in recipe.ingredients:
        for member in family:
            if ing.allergen_tags & member.allergens:
                violations.append(Violation(
                    severity="block",
                    member=member.name,
                    ingredient=ing.name,
                    reason=f"アレルゲン: {ing.allergen_tags & member.allergens}",
                ))
            if ing.normalized in member.dislikes:
                violations.append(Violation(
                    severity="warn",
                    member=member.name,
                    ingredient=ing.name,
                    reason="苦手食材",
                ))
    return violations
```

### 食材名の正規化が地味な難所

LLMは「えび」「エビ」「海老」「シュリンプ」「小エビ」をバラバラに出してくる。正規化辞書が必要:

```python
INGREDIENT_ALIAS = {
    "エビ": ["えび", "海老", "shrimp", "小えび", "むきえび", "ブラックタイガー"],
    "カニ": ["かに", "蟹", "crab", "ズワイガニ", "タラバガニ"],
    "卵": ["たまご", "玉子", "鶏卵", "egg", "うずら卵"],
}

ALLERGEN_GROUPS = {
    "甲殻類": {"エビ", "カニ"},
    "ナッツ": {"ピーナッツ", "アーモンド", "くるみ"},
}
```

消費者庁の特定原材料（8品目）+ 特定原材料に準ずるもの（20品目）を基盤に構築。

### テストでガードを固める

```python
def test_エビアレルギーの家族にエビ料理は絶対に通さない():
    family = [FamilyMember("妻", allergens={"甲殻類"}, dislikes=set())]
    recipe = Recipe(name="エビチリ", ingredients=[
        Ingredient("むきえび", "エビ", {"甲殻類", "エビ"}),
    ])
    violations = validate_recipe(recipe, family)
    blocks = [v for v in violations if v.severity == "block"]
    assert len(blocks) == 1
    assert blocks[0].member == "妻"
```

---

## 5. LLMへの情報提供: 効率と安全の二枚舌

### 疑問

最初のレシピ生成時にもLLMにアレルギーを渡す？ その後のガードレールだけでよい？

### 結論: 両方必要

LLMにも渡す、でもそれは「効率と品質」のため、「安全」のためではない。

### ガードレールのみだと非効率

```
LLM: 「エビチリどうですか？」
ガード: ブロック
LLM: 「エビグラタンは？」
ガード: ブロック
...
```

LLMは何も知らないので延々とNG料理を提案し続け、無駄な往復が発生する。

### 役割分担

| レイヤー | 目的 | 失敗時の挙動 |
|---------|------|------------|
| LLMへの指示 | 効率: 最初から妥当な提案をさせる | 失敗してもOK、次で捕まる |
| ガードレール | 安全: 絶対に通さない | 失敗は許されない |

インフラで言えば:
- LLMへの指示 = アプリ側バリデーション（UX のため）
- ガードレール = DB制約 / ファイアウォール（整合性のため）

### プロンプトは重要度を明示

```
# 家族情報

## 🚨 絶対禁止（アレルギー）
- 妻: 甲殻類（エビ・カニ・その加工品すべて）
- 子: 卵、ピーナッツ
これらを含むレシピは提案しないでください。

## 嫌いな食材（できれば避ける）
- 夫: セロリ、パクチー
- 妻: しいたけ

## 直近の献立（被り回避）
- 2026-04-17: 鶏の照り焼き
- 2026-04-16: 鮭のムニエル

# ルール
- アレルギー食材は「見落とし」ではなく「絶対ダメ」です
- 加工品（練り物、スープの素など）も原材料を考えてください
```

### ガード発動はプロンプト品質のメトリクス

```python
if blocks:
    logger.warning(
        "LLMがアレルゲン含むレシピを提案",
        extra={
            "recipe": suggestion.name,
            "violations": blocks,
            "prompt_version": "v3",
        }
    )
```

Loki + Grafana で可視化。**ガード発動率が0に近いほどプロンプト品質が良い**という定量指標。

---

## 6. 全体フロー

```
[ユーザー] 「今晩の夕飯どうしよう」
    ↓
[Python] 履歴集計・在庫取得・アレルギー取得
    ↓
[Python] 候補レシピを事前フィルタ
  - アレルゲン含むレシピを除外
  - 直近7日に作ったレシピを除外
    ↓
[LLM] フィルタ済み候補から選定・提案
  system: アレルギー・嫌いなもの・履歴サマリ・ルール
  user: 在庫 + 候補レシピ + 「今晩の提案を3つ」
    ↓
[Python] LLM出力を再検証（二重チェック）
  - アレルゲンチェック
  - スキーマ検証
    ↓
[ユーザー] 提案表示 → 選択 → 買い物リスト生成
```

**事前フィルタ + LLM提案 + 事後検証** の三段構え。

---

## 7. モデル選定

### Claude Sonnet 4.6 で十分

家庭用レシピ提案のコンテキスト量:

- 家族プロファイル: 〜1KB
- 直近4週間の履歴サマリ: 〜2KB
- 事前フィルタ済み候補レシピ: 〜20-50KB
- 在庫情報: 〜2KB
- システムプロンプト・ルール: 〜3KB
- **合計: 〜20k tokens**

Sonnet 4.6は200k tokens（1M対応版もあり）なので余裕。

### Orchestrator + Worker パターン

**Sonnet(計画) + Haiku(詳細化 並列)** が王道かつ筋の良い設計。

```python
import asyncio
from anthropic import AsyncAnthropicVertex

client = AsyncAnthropicVertex(region="asia-northeast1", project_id="...")

# Step 1: Sonnet で献立構成
async def plan_menu(context: MenuContext) -> MenuPlan:
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=build_system_prompt(context),
        messages=[{
            "role": "user",
            "content": "今晩の献立を主菜1・副菜1・汁物1で提案してください。"
                       "メニュー名と選定理由のみをJSONで返してください。"
        }],
    )
    return parse_menu_plan(response)

# Step 2: Haiku で各メニューの詳細を並列生成
async def elaborate_dish(dish_name: str, context: MenuContext) -> Recipe:
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        system="レシピ化担当。料理名から材料と手順をJSONで出力。",
        messages=[{
            "role": "user",
            "content": f"料理名: {dish_name}\n人数: {context.servings}人\n"
                       f"避ける食材: {context.allergens}\n"
                       f"在庫で優先使用: {context.pantry}"
        }],
    )
    return parse_recipe(response)

# 全体フロー
async def suggest_dinner(context: MenuContext) -> DinnerProposal:
    plan = await plan_menu(context)
    recipes = await asyncio.gather(*[
        elaborate_dish(dish.name, context) for dish in plan.dishes
    ])
    for recipe in recipes:
        violations = validate_recipe(recipe, context.family)
        if any(v.severity == "block" for v in violations):
            raise AllergenViolation(...)
    return DinnerProposal(plan=plan, recipes=recipes)
```

### 分担のメリット

**並列化で速い**

```
[直列] Sonnetで全部生成: 15秒
[分担] Sonnetでメニュー名: 3秒 + Haiku 3並列: 2秒 = 5秒（3倍速）
```

**コストが1/3〜1/4**

適材適所。Sonnet は戦略、Haiku は量産。

### 注意点

1. **Context引き継ぎ**: Sonnetが知ってる前提（アレルギー等）をHaikuにも漏れなく渡す
2. **Haiku出力にもガードレール必須**: Haikuは見落とし率がSonnetより高い可能性
3. **料理名の解釈ズレ対策**: Sonnet出力に主要食材も含めさせる
4. **候補レシピを計画段階で渡す**: 「なんでもあり」より「リストから選ぶ」が安定

### 将来の3層構成

```
Opus 4.7    : 週間献立プランニング（週1回・戦略レベル）
Sonnet 4.6 : 日次の献立構成（毎日・戦術レベル）
Haiku 4.5  : レシピ詳細化・買い物リスト生成（量産・実行レベル）
```

---

## 8. API料金試算

### 現在のAPI料金（2026年4月時点）

| モデル | Input | Output |
|--------|-------|--------|
| Claude Opus 4.7 | $5 / MTok | $25 / MTok |
| Claude Sonnet 4.6 | $3 / MTok | $15 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $5 / MTok |

### 月額試算（1日3食 × 30日 = 90回/月）

| パターン | 月額 | 年額 |
|---------|------|------|
| 全Haiku（最安） | 480円 | 5,800円 |
| Sonnet + Haiku | 1,050円 | 12,600円 |
| + Prompt Cache適用 | **400円** | **4,800円** |
| 全Sonnet | 1,430円 | 17,200円 |

月数百円〜千円台。Netflixサブスクより安い。家族4人の食事の質が上がるなら十二分にペイ。

### 現実的な節約ポイント

1. **朝昼は省略**: 晩だけで月額1/3に
2. **Prompt Caching を必ず実装**: 設計変更は軽微、効果大
3. **履歴集計はPython事前処理**: 生履歴をLLMに渡さず集計済みサマリだけ
4. **レシピ詳細はDBから引く**: Sonnetが「肉じゃが（ID:42）」指定 → DB lookup
5. **Vertex AIのContext Caching併用**

### Vertex AI特有の考慮

- Anthropic直接APIより若干高い場合あり（GCP管理料）
- ただしGCP内完結メリット（IAM、VPC、請求統合）
- 既存GCPインフラ（NetBird管理サーバー等）と同プロジェクトで請求管理が楽

---

## 9. Prompt Caching 活用

### 仕組みの本質

**Anthropic API側の機能**で、プロンプトの「共通部分」をAnthropic側にキャッシュしてもらう。自前でレシピをキャッシュする話とは**別物**。

```
通常:
  毎回 20k tokens 送信 → 毎回 $3/MTok で課金

Caching:
  1回目: 20k 送信、うち15kを cache breakpoint でマーク
         → cache書き込み（25%増し課金）
  2回目以降: 同じ15k部分は $0.30/MTok（90% off）で課金
            変動部分5kだけ通常課金
```

### キャッシュ対象の条件

**リクエスト間で変わらない、大きい、頻繁に使う** の3条件。

### Sonnet のプロンプト構造

```
┌─────────────────────────────────┐
│ [CACHE対象] システムプロンプト    │ ← ほぼ不変
├─────────────────────────────────┤
│ [CACHE対象] 家族プロファイル      │ ← 月単位で変わる程度
├─────────────────────────────────┤
│ [CACHE対象] 候補レシピ集         │ ← 新レシピ追加時のみ変わる
├─────────────────────────────────┤
│ ← ここに cache breakpoint       │
├─────────────────────────────────┤
│ [毎回変動] 今日のコンテキスト     │ ← リクエストごとに違う
│ - 直近2週間の履歴サマリ           │
│ - 現在の在庫                    │
│ - 今日の要望                    │
└─────────────────────────────────┘
```

### TTLと料金

- **Cache TTL**: デフォルト5分（利用ごとに延長）、1時間拡張オプションあり
- **Cache write**: 通常input価格の 1.25倍（初回ペナルティ）
- **Cache read**: 通常input価格の 0.1倍（90% off）
- breakpointは最大4つ設定可能

### 実装例

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2000,
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        },
    ],
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": FAMILY_PROFILE + RECIPE_LIBRARY,
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": today_context,  # キャッシュしない
                },
            ],
        }
    ],
)
```

### 二層キャッシュ戦略

| レイヤー | 何を | どこに | 効果 |
|---------|------|-------|------|
| アプリキャッシュ | 生成済みレシピ本体 | 自前DB | APIコール自体を削減 |
| Prompt Caching | 共通プロンプト部分 | Anthropic側 | APIコールの料金を削減 |

### 運用時のフロー

```
1. ユーザーから「今晩の献立」リクエスト
   ↓
2. Sonnet で献立計画
   → Prompt Caching: 家族情報+レシピ集+ルール（90% off）
   → 出力: [肉じゃが, ほうれん草のおひたし, 味噌汁]
   ↓
3. 各品の詳細取得（3並列）
   ├─ 「肉じゃが」→ DB hit → 即返す（API呼ばない）
   ├─ 「ほうれん草のおひたし」→ DB hit → 即返す
   └─ 「いつもと違う味噌汁」→ DB miss → Haiku生成
                           → Prompt Caching効かせる
                           → 結果をDBに保存
   ↓
4. ガードレール検証
   ↓
5. ユーザーに提示
```

運用1年もすれば定番レシピがDBに貯まって、Haikuはほぼ呼ばれなくなる。

### Batch APIは使えるか

- 50% OFFだが結果最大24時間後
- リアルタイム性必要な「今晩何食べる」には使えない
- ただし**週次献立プランニング**（Opusで戦略レベル）なら batch 向き

---

## 10. システムの肝: Sonnet計画フェーズ

### Sonnetの計画が全体品質を決める

このシステムで「賢さ」が問われる判断はすべて計画フェーズに集中:

- アレルギー・嫌いを全員ぶん同時に満たす組み合わせ選び
- 直近履歴との被り回避（メタ認知）
- 主菜・副菜・汁物の調和（味・色・調理法の重複回避）
- 栄養バランス・季節感
- 在庫の有効活用
- 家族のコンディション考慮

これらは全体を俯瞰しないと判断できないタスクで、Sonnetの強みが生きる領域。

Haikuの詳細化は「肉じゃがのレシピを出す」という孤立した定型作業。ガードレールもコードで担保。ここの品質は全体への影響が小さい。

### 投資配分

| フェーズ | 投資すべきリソース |
|---------|----------------|
| **Sonnet計画** | プロンプトエンジニアリング・評価・改善に集中投資 |
| Haiku詳細化 | そこそこの品質で回ればOK、定型化 |
| ガードレール | 確実性重視、テストで固める |
| データ構造 | 計画フェーズが使いやすい形に整える |

### 磨き続けるべきポイント

- 履歴の集計の仕方（生データじゃなく「今週の主菜カテゴリ: 鶏8・魚3」形式）
- 家族情報の構造化（アレルギー・嫌い・好きの優先度明示）
- 候補レシピの絞り方（多すぎても少なすぎてもダメ）
- ルールの書き方（🚨絶対・推奨・参考の階層）
- 出力フォーマット設計（後段処理が使いやすいJSON schema）

### 運用面での示唆

- **プロンプトはgit版管理**: Sonnet側system promptは育てる資産
- **評価データセット作成**: 「この家族情報・履歴ならこういう提案が妥当」というゴールデンセット
- **提案ログ全保存**: プロンプト改善の材料
- **家族のフィードバック回収導線**: 「この提案どうだった？」を気軽に記録できるUI
- **Grafanaで満足度推移可視化**: Flask側で提案履歴テーブル + フィードバックテーブル

### 完成形イメージ

1年運用後:

- 家族固有の食嗜好を学習したプライベート管理栄養士のような存在に
- 定番レシピ200品超がDB蓄積、Haikuの出番は新規開拓時だけ
- 「あのとき美味しかったやつまた作って」が通じる
- 献立の偏り・栄養バランスがGrafanaで可視化
- 食材廃棄削減の経済インパクト

---

## 11. アーキテクチャサマリ

### スタック想定

- **LLM**: Vertex AI経由で Claude Sonnet 4.6 (計画) + Haiku 4.5 (詳細化)
- **アプリ**: Flask + SQLAlchemy（既存の内部ツール方針に寄せる）
- **データ永続化**:
  - レシピ・履歴・家族情報: DB
  - 初期プロトタイプ: Markdown + Git
- **認証**: Authentik SSO（既存IdP）
- **監視**: Zabbix + Grafana + Loki（既存スタック）
- **デプロイ**: Proxmox上のLXC or VM

### データモデル（概念）

```
- FamilyMember (id, name, allergens[], dislikes[], likes[])
- Ingredient (id, name, normalized, allergen_tags[])
- Recipe (id, name, main_ingredient, ingredients[], steps, tags[])
- Menu (id, date, dishes[], proposed_by, feedback)
- History (recipe_id, cooked_at, rating, notes)
- Pantry (ingredient_id, quantity, expires_at)
```

### 三段階処理

1. **事前フィルタ**（Python/SQL）
   - アレルゲン除外
   - 直近7日の履歴除外
   - 候補レシピ絞り込み

2. **LLM提案**（Vertex AI）
   - Sonnet: 全体計画 + Prompt Cache
   - Haiku: 詳細化 並列実行

3. **事後検証**（Python）
   - ガードレール（アレルゲン再チェック）
   - スキーマ検証
   - ログ記録（プロンプト品質メトリクス）

---

## 12. 実装優先順位（提案）

### Phase 1: MVP
- 家族情報・レシピ・履歴のデータモデル定義
- アレルゲンチェックのコアロジック + テスト
- 食材正規化辞書の初期版
- Sonnet単体での提案プロトタイプ（Haiku分担前）

### Phase 2: 本運用
- Sonnet + Haiku 分担アーキテクチャ
- Prompt Caching 実装
- ガードレール完全版
- Flask UI（提案・フィードバック・履歴閲覧）

### Phase 3: 洗練
- 買い物リスト生成
- 在庫管理連携
- Grafana ダッシュボード（満足度・ガード発動率）
- 評価データセット構築 → プロンプト改善サイクル

### Phase 4: 拡張
- Opus による週間プランニング（戦略層）
- 栄養バランス分析
- レシピ画像生成・取り込み
- 家族メンバーごとの個別カスタマイズ

---

## 付録: 重要な設計原則

1. **LLMを信じないが、最大限活用する**（二枚舌アーキテクチャ）
2. **命に関わる判断は決定論的に**（アレルギーはコードで守る）
3. **LLMはcreative、コードはcorrect**（責務の明確化）
4. **構造化して渡す**（生データじゃなく集計済みで）
5. **Sonnet計画フェーズが肝**（ここに投資を集中）
6. **ガード発動率は品質メトリクス**（プロンプト改善の指針）
7. **二層キャッシュ**（アプリDB + Prompt Caching）
8. **育てる資産としてのプロンプト**（Git管理・版管理）

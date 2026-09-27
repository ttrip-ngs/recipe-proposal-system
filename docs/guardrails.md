# ガードレール設計

## 1. 設計思想

LLM の出力を、LLM を使わずに機械的に検証する。理由は以下。

- 確率的動作: 同じ入力でも違う結果が出る可能性
- プロンプトインジェクション耐性なし
- コスト・レイテンシが毎回発生
- テストが書きにくい

対してコード検証は以下を提供する。

- 決定論的（同じ入力 → 必ず同じ出力）
- ユニットテスト可能
- ミリ秒で完了
- 監査可能

インフラ用語で言えば「ACL とファイアウォール」。LLM はサジェスタ、deny は決定論的ルールエンジン。

本システムではアレルゲン検証を **決定論的コードでのみ実施する**。LLM への委譲は禁止（CLAUDE.md §6.1 参照）。

## 2. 食材正規化辞書

### 2.1 配置

```
src/recipe_system/guardrails/dictionaries/
  ├── aliases.yaml      # canonical 名 <-> alias の対応
  └── allergens.yaml    # allergen group <-> canonical 名の集合
```

`data/normalizer/` ではなく `src/` 配下に置く。辞書とアルゴリズムは一体で、パッケージング・型付けの観点から同居が適切。

### 2.2 スキーマ

`aliases.yaml`

```yaml
version: "1"
last_reviewed_at: "2026-04-18"
entries:
  - canonical: エビ
    aliases: [えび, 海老, shrimp, 小えび, むきえび, ブラックタイガー]
    source: consumer-agency-2023
  - canonical: カニ
    aliases: [かに, 蟹, crab, ズワイガニ, タラバガニ]
    source: consumer-agency-2023
  - canonical: セロリ
    aliases: [せろり, celery]
    source: household-custom
```

`allergens.yaml`

```yaml
version: "1"
last_reviewed_at: "2026-04-18"
groups:
  - name: 甲殻類
    members: [エビ, カニ]
    source: consumer-agency-2023
  - name: ナッツ
    members: [ピーナッツ, アーモンド, くるみ, カシューナッツ]
    source: consumer-agency-2023
```

`source` フィールドで出典（消費者庁 / 家庭独自）を区別する。更新時は `last_reviewed_at` と `version` をインクリメントする。

1 つの表記（canonical 名・別表記、大文字小文字を区別しない）は 1 つの canonical にだけ登録する。
重複があるとローダーが `ValueError` で起動を止める。後勝ちで上書きすると、片方の canonical が
持つアレルゲングループが黙って失われるため（2026-09 に「味噌」が `大豆` の別表記と canonical
`味噌` に二重登録され、大豆タグが付かなかった）。加工品を苦手食材の指定用に独立 canonical として
持つ場合は、アレルゲンとの対応を `allergens.yaml` の `members` に書く（例: `大豆: [大豆, 味噌]`）。

### 2.3 ベースライン

消費者庁の特定原材料 8 品目（卵・乳・小麦・えび・かに・そば・落花生・くるみ）と、表示推奨される特定原材料に準ずるもの 20 品目の合計 28 品目を Phase 1 の必須カバー範囲とする。

### 2.4 家庭独自の嫌いな食材

家族メンバー個別の嫌いな食材（例: 夫が嫌いなセロリ）は `families/{id}/members/{id}.dislikes` に canonical 名で格納する。辞書とは分離することで、辞書は世帯間で共有可能な知識ベースに保つ。

## 3. 正規化アルゴリズム

LLM 出力の食材名 `raw_name` を canonical 名に変換し、所属する allergen_group の集合を付与する。

```
def normalize(raw_name: str) -> Ingredient:
    lower = raw_name.strip().lower()
    canonical = ALIAS_INDEX.get(lower) or raw_name
    allergen_tags = set()
    for group_name, members in ALLERGEN_GROUPS.items():
        if canonical in members:
            allergen_tags.add(group_name)
    return Ingredient(
        name=raw_name,
        canonical=canonical,
        allergen_tags=allergen_tags,
    )
```

辞書未ヒット時は canonical に `raw_name` をそのまま格納し、allergen_tags は空集合となる。このケースは未知食材として扱う（§5）。

## 4. 決定論的検証

### 4.1 Violation 型

```python
@dataclass
class Violation:
    severity: Literal["block", "warn"]
    member: str
    ingredient: str
    reason: str
```

- `block`: アレルゲンヒット。提案を通さない
- `warn`: 嫌いな食材。ログに残すが提案は通す

### 4.2 validate_recipe

```python
def validate_recipe(recipe: Recipe, family: list[FamilyMember]) -> list[Violation]:
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
            if ing.canonical in member.dislikes:
                violations.append(Violation(
                    severity="warn",
                    member=member.name,
                    ingredient=ing.name,
                    reason="苦手食材",
                ))
    return violations
```

`block` が 1 件でもあれば提案を拒否する。`warn` は提案を通すが構造化ログに記録する。

### 4.3 手順 (steps) の文章検査 (2026-09 追加)

LLM が生成する作り方 (`steps`) は自由文のため、食材リストのような完全一致の正規化では
検査できない。手順は食材リストと同じ LLM 呼出で生成させ「食材リストにない食材を書かない」
よう指示しているが、指示だけでは保証できない (「溶き卵でとじる」のように手順にだけ
アレルゲンが現れうる)。そこで `validate_recipe` は `recipe.steps` があれば次を行う。

1. メンバーの `allergens` (グループ名 / canonical 名) から、対象 canonical と全エイリアスを
   集める (`NormalizerDictionary.text_terms_for`)。家族のアレルギーに関係する語だけに絞る
2. 各ステップの文章 (小文字化) にその語が部分一致すれば `block` (`reason` に該当ステップを含める)

日本語は分かち書きされないため部分一致になり、短い語は無関係な語の一部として誤検出する
(「フライパン」の「パン」、「なめらかに」の「かに」など)。これは `aliases.yaml` の
`text_match_exclude` に語を載せて外す。除外はコードではなく辞書 (データ) で行い、人間が
レビューする。除外は語単位で、同じ食材の別表記 (「パン粉」など) は引き続き検出される。

手順の検出も通常の `block` と同じく単日修復 (最大 1 回、§6) に回る。フェイルオープンしない。
実測では Opus 5.5 の手順 279 件で、サンプル家族 (卵・落花生・甲殻類) の語による誤検出は 0 件
(memo/history/026)。

## 5. 未知食材ポリシー

LLM 出力に辞書未登録の食材が現れた場合、既定では以下の扱いとする。

- `warn` ログに記録（`unknown_ingredient=true`）
- 提案は通す（`block` にしない）
- 週次のレビューで人間が判断し、必要なら `aliases.yaml` に追加

`block` にしない理由は、辞書網羅性が低い初期段階では全提案が止まってしまうため。運用で辞書を育てる方針を取る。

将来的に辞書カバレッジが十分になったら、環境変数 `UNKNOWN_INGREDIENT_POLICY=block` への切り替えを検討する。

## 6. リトライ戦略

### 6.1 最大 1 回

ガード違反時、LLM への再依頼は最大 1 回とする。2 回目以降はフェイルオープンせず、UI に「安全な提案を作れませんでした。手動で選択してください」と提示する。

### 6.2 リトライプロンプト

再依頼時は違反内容をプロンプトに追記する。

```
previous_violations:
  - member: 妻
    ingredient: むきえび
    reason: アレルゲン: 甲殻類
前回の提案には上記違反が含まれていました。
これらを含まない別の献立を提案してください。
```

### 6.3 禁止事項

- 無限リトライ
- リトライ時にルールを自動緩和（例: アレルゲンチェックを無効化）
- ガード違反を無視して提案を通す

### 6.4 ログ

リトライの発生有無、違反内容、プロンプトバージョン、最終結果をすべて `proposals/{proposalId}` に保存する。

## 7. テスト方針

### 7.1 配置

`tests/guardrails/` に独立配置する。他テストと別フォルダにすることで、カバレッジ閾値を個別に高く設定できる（目標 95% 以上）。

### 7.2 必須テストケース

- 消費者庁 28 品目の各アレルゲンに対して `block` violation が返ること
- 家族全員のアレルゲンが同時に検証されること
- エイリアス全てに対して正規化が効くこと
- 未知食材で `block` にならず warn ログのみであること
- リトライ発生時のログ記録
- 初期家族プロファイルに対する E2E テスト（「この家族なら甲殻類を含むレシピは提案されない」）
- 手順の文章にだけ現れたアレルゲン (エイリアス表記を含む) で `block` になること
- `text_match_exclude` の語 (フライパン / なめらかに) で誤検出しないこと

### 7.3 サンプル

```python
def test_エビアレルギーの家族にエビ料理は絶対に通さない():
    family = [FamilyMember("妻", allergens={"甲殻類"}, dislikes=set())]
    recipe = Recipe(
        name="エビチリ",
        ingredients=[Ingredient("むきえび", "エビ", {"甲殻類", "エビ"})],
    )
    violations = validate_recipe(recipe, family)
    blocks = [v for v in violations if v.severity == "block"]
    assert len(blocks) == 1
    assert blocks[0].member == "妻"
```

## 8. メトリクス

- `guard_trigger_rate` = `block 発動した提案数 / 総提案数`
  - 0 に近いほどプロンプト品質が高い
- `warn_rate` = `warn 付き提案数 / 総提案数`
  - 嫌いな食材の取りこぼし指標
- `unknown_ingredient_rate` = `未知食材を含む提案数 / 総提案数`
  - 辞書網羅性の指標

詳細は [evaluation.md](evaluation.md) と [operations.md](operations.md)。

## 9. 関連ドキュメント

- [llm-integration.md](llm-integration.md) - LLM 呼び出し仕様
- [evaluation.md](evaluation.md) - ガードレールの回帰テスト
- [data-model.md](data-model.md) - `proposals` コレクションのスキーマ

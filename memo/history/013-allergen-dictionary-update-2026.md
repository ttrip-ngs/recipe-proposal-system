# 013 - アレルゲン辞書 2026 年改正対応とメンバー編集 UI 改善

実施日: 2026-05-24
ブランチ: `feature/dashboard-meal-plans-20260524`

## 背景

メンバー編集画面のアレルゲンチェックボックスについて, ユーザーから「山芋
など抜けている物がある」との指摘を受けた. 実際には `allergens.yaml` のグ
ループ名「芋類」配下に `やまいも` (山芋・長芋・自然薯) として登録済みで
あったが, UI 上は「芋類」というラベルのみが表示され, 何が含まれている
かユーザーから判別できない状態であった.

加えて消費者庁による特定原材料等の改正が複数進んでおり, 現行辞書は
2024-03-28 改正 (マカダミアナッツ追加・まつたけ削除) と 2026-04-01 改正
(カシューナッツの義務化・ピスタチオ追加) を取り込めていなかった.

## 公式ソース調査結果

消費者庁「食品表示基準」食物アレルギー表示の対象品目 (令和 8 年 4 月時点):

- 特定原材料 (表示義務 9 品目): 卵, 乳, 小麦, そば, 落花生, えび, かに,
  くるみ, カシューナッツ
- 特定原材料に準ずるもの (表示推奨 20 品目): アーモンド, あわび, いか,
  いくら, オレンジ, キウイフルーツ, 牛肉, ごま, さけ, さば, 大豆, 鶏肉,
  バナナ, 豚肉, マカダミアナッツ, もも, やまいも, りんご, ゼラチン,
  ピスタチオ

改正履歴:

- 2023-03-09: 義務にくるみ追加
- 2024-03-28: 推奨にマカダミアナッツ追加, まつたけ削除
- 2026-04-01: 義務にカシューナッツ昇格, 推奨にピスタチオ追加
  (経過措置期間 2028-03-31 まで)

## 設計判断

### グループキーの安定性

`member.allergens` は Firestore に **グループ名の文字列の集合** として
保存される. グループ名のリネームは既存ユーザーのアレルギー検証を静かに
無効化するリスクがある (validator は `ingredient.allergen_tags &
member.allergens` で集合比較しているため).

現状は production データがなく example seed (`family.example.yaml`) も
「芋類」「きのこ」キーを使用していないため, 早期にリネームを実施
(「芋類」→「やまいも」). 今後本番運用後の変更時には migration を伴うこと.

### まつたけの扱い

公式リストから削除されたが, まつたけアレルギーを持つ人にとっては引き続き
危険食材. CLAUDE.md 6.1「アレルゲン検証は決定論的コードで実施」の精神に
照らし, グループとして辞書に維持. `source` を `consumer-agency-2026` から
`household-safety` に降格して「公式リスト外だが安全のため保持」と明示する.

### ナッツグループの拡張

「ナッツ」グループは義務 (くるみ・カシューナッツ) と推奨 (アーモンド・
マカダミアナッツ・ピスタチオ) を横断するが, 木の実類は交差反応リスクが
高いため一括チェックを優先する設計を維持. allergens.yaml のコメントに
明示した.

### UI 改善

- `profile/edit.html`: アレルゲンチェックボックスにグループ内訳の補足
  テキストを併記 (例: 「ナッツ / アーモンド · カシューナッツ · くるみ ·
  ピスタチオ · マカダミアナッツ」). タッチ領域も従来の 6px パディングから
  `--touch-comfortable` ベースに引き上げ.
- `profile/show.html`: アレルゲン chip にメンバー名を併記し,
  `title` 属性でも一覧を提供.

## 変更ファイル

### 辞書

- `src/recipe_system/guardrails/dictionaries/allergens.yaml`
  - version "1" → "2", last_reviewed_at 2026-05-24
  - 「芋類」→「やまいも」リネーム
  - 「ナッツ」に マカダミアナッツ・ピスタチオ 追加
  - 「きのこ」→「まつたけ」リネームし source を `household-safety` に降格
  - 全エントリの source を `consumer-agency-2023` → `consumer-agency-2026`
  - ヘッダーコメントに公式ソース・改正履歴・設計上の補足を明示
- `src/recipe_system/guardrails/dictionaries/aliases.yaml`
  - version "1" → "2", last_reviewed_at 2026-05-24
  - カシューナッツを義務 9 品目セクションに移動
  - マカダミアナッツ (aliases: macadamia, マカダミア, マカデミアナッツ) 追加
  - ピスタチオ (aliases: pistachio, ピスタチオナッツ) 追加
  - まつたけ source を `household-safety` に降格
  - やまいも aliases に「とろろ」追加

### Web レイヤー

- `src/recipe_system/web/routes/profile_edit.py`:
  `allergen_groups` を `[{name, members}, ...]` 形式に変更
- `src/recipe_system/web/templates/profile/edit.html`:
  チェックボックスをメンバー併記表示に改修, タッチ領域を改善
- `src/recipe_system/web/routes/profile.py`:
  `allergen_group_members` をテンプレートコンテキストへ追加
- `src/recipe_system/web/templates/profile/show.html`:
  アレルゲン chip にメンバー併記と `title` 属性

### テスト

- `tests/guardrails/test_dictionary_loader.py`:
  - `test_消費者庁_特定原材料_9_品目がすべて辞書に含まれる`
    (カシューナッツ追加対応)
  - `test_消費者庁_推奨表示の新規追加品目が辞書に含まれる`
    (マカダミアナッツ・ピスタチオ)
  - `test_やまいもの表記揺れが正規化される`
  - `test_まつたけは公式リスト外でも辞書に維持される`

## テスト結果

- `uv run pytest tests/guardrails tests/unit`: 72 passed
- `uv run ruff check --fix src tests`: clean
- `uv run ruff format src tests`: 73 files unchanged
- `uv run mypy src`: no issues found

## 後続課題

- production 投入後にグループキーを変更する際は migration スクリプトを
  必ず作成すること (CLAUDE.md 6.4)
- ピスタチオの食品表示義務化への昇格動向を注視
- 大麦・ライ麦などの小麦交差反応, アボカドなどの口腔アレルギー症候群対象は
  今のところ家庭辞書 (`household-custom`) で対応する想定. 必要に応じて
  追加していく.

## 追加対応: 個別食材ベース指定への全面移行 (同日)

ユーザーから「ナッツなど複数食材入っているものは一括チェックしかできないが,
くるみだけアレルギーの場合は全部のナッツが食べられないのと同じ扱いで良いか」
との指摘. 医療的には「アレルギーがあると診断された食材のみを除去」が原則で,
交差反応を理由に医師指示なく他のナッツを除去するのは推奨されない. グループ
一括方式は **過剰除去** を生み, 献立多様性を不必要に狭める弱点があった.

### 設計判断

`ingredient.allergen_tags` を **「所属グループ名集合 + canonical 自身」** に
拡張する形で対応 (`guardrails/dictionary_loader.py:normalize`). これにより:

- `member.allergens = {"くるみ"}` (canonical 個別指定) → ingredient tag に
  "くるみ" が含まれるためヒット, "アーモンド" は別 canonical なのでヒットせず.
- `member.allergens = {"ナッツ"}` (グループ一括) → ingredient tag に
  "ナッツ" が含まれるため引き続きヒット.

validator/filter ロジックは集合演算のまま変更不要で, **後方互換性も維持**.

未登録食材は誤検出を避けるため canonical 拡張を行わず空 tag を返す
(テストで保護).

### UI 変更

`partials/_macros.html` に `allergen_checkboxes` マクロを新設. グループ単位
で fieldset を分け, グループ内に各 canonical の個別チェックボックスを配置.
グループヘッダーに「全選択」「解除」ボタン (data-allergen-toggle) を添え,
`static/js/app.js` の `setupAllergenGroupToggles` で JS 制御.

`profile/edit.html` の編集フォーム / 新規追加フォーム双方をマクロ呼び出しに
置換 (DRY). `profile/show.html` は canonical 名がそのまま chip 表示で意味を
なすため, グループ展開表示は撤去してシンプルな chip に戻し, `profile.py`
ルートも不要になった `allergen_group_members` 渡しを撤去.

`plans/day.html`, `plans/week_review.html`, `proposals/detail.html` の
allergen_tags 表示は canonical 自身が tag に追加されたことで二重表示に
なる恐れがあるため, `{% for tag in ing.allergen_tags if tag != ing.canonical %}`
でフィルタ.

### Seed 更新

`data/seeds/family.example.yaml` の妻メンバーを `allergens: [甲殻類]` から
`allergens: [エビ, カニ]` に展開. ヘッダーコメントに「canonical 名で個別
指定する方が献立多様性を保てる」と明記.

### テスト

- `tests/guardrails/test_validators.py` に 2 ケース追加
  - `test_canonical_個別指定で該当食材だけがblockされる`
  - `test_グループ名指定なら同グループ全食材がblockされる` (後方互換)
- `tests/guardrails/test_dictionary_loader.py` に 2 ケース追加
  - `test_normalize_は_canonical_自身もタグに含む`
  - `test_未登録食材は_canonical_を_tag_に含めない` (誤検出防止)

### 結果

- `uv run pytest`: 76 passed
- `uv run ruff check / format`: clean
- `uv run mypy src`: no issues found

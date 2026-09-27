# 029 未知食材の warn ログを実装

2026-09-27

## 背景

CLAUDE.md 6.6 と docs/guardrails.md §5 は「辞書未登録の食材は warn ログに記録し、週次で人間が
レビューして aliases.yaml に追加する」と定めているが、実装されていなかった
(`NormalizerDictionary.is_known` はどこからも呼ばれていなかった)。未知食材はアレルゲンタグが
空になるため、辞書の穴を見つける手がかりが無い状態だった。

## 対応

- `suggestion_common.normalize_dish` で `is_known` を判定し、未知食材を含む料理ごとに
  `guard.unknown_ingredient` を warn で出す。単日提案・週間提案 (修復を含む) の全経路が通る
- フィールド: `unknown_ingredient=true`, `dish`, `ingredients` (未知食材名の一覧), `aliases_version`。
  家族情報は含めない
- 提案は止めない (block にしない) 方針は従来どおり

出力例:

```
{"unknown_ingredient": true, "dish": "豚汁", "ingredients": ["ごぼう", "こんにゃく"],
 "aliases_version": "3", "event": "guard.unknown_ingredient", "level": "warning", ...}
```

## 残課題

- ガードレール辞書は調味料・一般的な野菜を持たないため、塩・ごぼう等も毎回記録される。
  週次レビューでは集計して頻度順に見る (Jev による候補付けは別途)
- `UNKNOWN_INGREDIENT_POLICY=block` は設定値だけあり、動作は未実装 (docs §5 で将来検討扱い)
- docs/guardrails.md §8 の `unknown_ingredient_rate` メトリクスは未実装

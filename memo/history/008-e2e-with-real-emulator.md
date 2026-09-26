# 008: 実 Firestore エミュレータでの E2E 動作確認と Deprecation 対応

- 日付: 2026-04-18
- 担当: Claude Code (Opus 4.7)
- 前回: `007-git-init-and-emulator-notes.md`

## 概要

mise で Java (Temurin 17) を入れて Firestore エミュレータを実際に起動、integration テストを全て実通し、さらに E2E テスト (Firestore + フェイク LLM + 三段階処理) を追加。副次的に google-cloud-firestore の positional where deprecation 警告を修正。

## 実施内容

### Java 導入とエミュレータ起動

- `mise install java@temurin-17` で Temurin 17.0.18 をインストール
- `JAVA_HOME` / `PATH` を設定して `npx firebase-tools@13.29.1 emulators:start --only firestore` を実行
- Firestore エミュレータが 127.0.0.1:8080 で起動成功

### integration テストが実通し

```
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
GOOGLE_CLOUD_PROJECT=recipe-system-dev \
uv run pytest tests/integration
# -> 2 passed
```

### seed スクリプトの実地動作確認

```
$ uv run python scripts/seed_firestore.py --emulator
[mode] emulator (host=127.0.0.1:8080)
[family] example-family members=3
[recipes] inserted=5
[history] inserted=3
[done]
```

family + members + recipes + history がすべて書き込まれることを確認。

### E2E テスト追加: tests/integration/test_suggest_dinner_e2e.py

2 ケース:

1. **test_suggest_dinner_e2e**
   - Firestore エミュレータに E2E 家族 (妻=甲殻類アレルギー / 夫=セロリ嫌い) を書き込む
   - `get_family` / `list_recipes` / `recent_history` で repository から読み戻す
   - `SuggestContext` を構築して `suggest_dinner(ctx, llm=FakeVertexClient())` を実行
   - 三段階処理を通して `succeeded=True`、カテゴリが {主菜, 副菜, 汁物} で揃い、甲殻類タグが含まれないことを検証
2. **test_proposal_record_の書き込みと読み出し**
   - `save_proposal_record` → `update_proposal_record` → `get_proposal` の経路を確認
   - pending → ready 遷移と dishes の読み戻しまで一気通貫

### Firestore deprecation 対応

`google-cloud-firestore` の positional `where("field", "==", value)` は deprecated で `where(filter=FieldFilter("field", "==", value))` が推奨.
`UserWarning` が 2 箇所で出ていたため `FieldFilter` 形式に置換:

- `repository/history_repository.py`: `family_id == X` / `cooked_at >= threshold`
- `repository/family_repository.py`: `allowed_emails array_contains email`

警告なしで integration テストが pass するようになった。

### 最終状態

```
$ FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  GOOGLE_CLOUD_PROJECT=recipe-system-dev \
  uv run pytest
40 passed in 9.36s

$ uv run ruff check .        → All checks passed!
$ uv run mypy src            → Success: no issues found in 38 source files
```

テスト合計:
- unit/guardrails: 11 + 25 = 36
- integration: 4 (firestore_roundtrip 2 + suggest_dinner_e2e 2)
- **計 40 ケース全 pass**

## 残課題・次手

1. 本コミット (FieldFilter 対応 + E2E テスト) を dev に登録
2. リモートリポジトリ設定 (`git remote add origin`)
3. 本番 Firebase Web SDK config の取得と .env 投入
4. `gcloud run deploy --source .` のドライラン (課金 GCP)
5. `--live` Vertex AI ゴールデン評価 (課金)

## 参照

- 前回: `memo/history/007-git-init-and-emulator-notes.md`

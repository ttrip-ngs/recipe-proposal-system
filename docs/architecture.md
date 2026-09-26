# アーキテクチャ

## 1. システム目的とスコープ

家庭内のレシピ提案・買い物管理を行う。以下を最重要要件とする。

- アレルギーは絶対に見落とさない（決定論的コード検証で担保）
- 家族の嫌いな食材・好き嫌い・過去履歴を蓄積し、提案品質を継続的に向上させる
- 月内で同じレシピに偏らない

Phase 1 MVP では夕食提案（主菜・副菜・汁物）に限定し、家族 1 世帯・単一の Firestore プロジェクトで運用する。

## 2. 全体アーキテクチャ

```
[家族ユーザー (ブラウザ)]
        |
        | HTTPS (Firebase Auth ID トークン)
        v
[Cloud Run: FastAPI + Jinja2]
  | ダッシュボード /
  | カレンダー (週/月) /
  | 日別予定 /plans/{date} /
  | 買い物リスト /shopping/week /
  | 家族プロファイル編集 /profile/edit
  |                |                      |
  v                v                      v
[Firestore]   [Vertex AI / Claude]   [Cloud Logging]
 families         Sonnet 4.6            構造化ログ
 members          + Prompt Caching
 recipes          単日 / 週間 7 日分
 meal_plans       (Phase B)
 shopping_lists
 proposals (LLM 呼び出しログ)
 feedback / history (互換)
  |
  v
[Cloud Storage] (週次 export バックアップ)
```

`meal_plans` が「家族 x 日付」の真実のソースで、カレンダー UI とフィードバックの中心。`proposals` は LLM 呼び出しログとして残し、`shopping_lists` は週内の confirmed/cooked 献立から集約される (詳細は [adr/0004-meal-plans-as-primary-record.md](adr/0004-meal-plans-as-primary-record.md))。

- フロントは Jinja2 SSR。JavaScript は cold start 対策の polling 程度に留める
- 認証は Firebase Authentication の ID トークンをアプリ側で検証
- LLM は Vertex AI 経由で Claude Sonnet 4.6 を呼び出す（`anthropic[vertex]` SDK）
- 永続化は Firestore Native Mode

## 3. 三レイヤー構造

本システムは情報の重要度によって 3 つのレイヤーに分ける。命に関わる情報は上位レイヤーで確実に守る。

| レイヤー | 対象情報 | 実装 | 失敗時の挙動 |
|---|---|---|---|
| Layer 1: ハードルール | アレルギー、医療上の NG 食材 | コード（ガードレール） | 絶対に通さない |
| Layer 2: 強い選好 | 嫌いな食材、直近 2〜4 週の履歴 | system prompt の冒頭 | 次段のガードレールで捕捉 |
| Layer 3: 参考情報 | レシピ集、過去評価、在庫 | context として LLM に投入 | 品質劣化にとどまる |

詳細な責務分担は [guardrails.md](guardrails.md) を参照。

## 4. 三段階処理

```
[ユーザー要求]
   |
   v
[Python 事前フィルタ]
   - アレルゲン含有レシピ除外
   - 直近 7 日の履歴除外
   - 候補レシピ絞り込み
   |
   v
[LLM 提案 (Sonnet 4.6)]
   system: アレルギー・嫌いなもの・履歴サマリ・ルール
   user:   在庫 + 候補レシピ + 今日の要望
   |
   v
[Python 事後検証 (ガードレール)]
   - アレルゲン決定論的チェック
   - Pydantic スキーマ検証
   - 違反時は最大 1 回 LLM リトライ
   |
   v
[UI 提示・フィードバック収集]
```

事前フィルタで多くの NG を機械的に落とし、LLM は創造的な組み合わせに集中する。事後検証は二重チェックの最後の砦。

## 5. LLM とコードの責務分担

| 責務 | 担当 |
|---|---|
| バリエーション提案・季節感・栄養バランス | LLM |
| ハードな禁止ルール（アレルギー） | コード |
| 履歴の集計・統計 | コード |
| レシピ本文の自然言語解釈 | LLM |
| 食材の正規化・タグ付け | コード（辞書ベース） |
| 最終承認（提案受入） | コード |

LLM は creative、コードは correct。命に関わるルールは確率的なものに任せない。

## 6. 技術スタックサマリ

| 区分 | 採用 | 備考 |
|---|---|---|
| 言語 | Python 3.12 | 型ヒント・非同期・Pydantic v2 |
| Web | FastAPI + Jinja2 | SSR、非同期 LLM 呼び出し |
| LLM | Claude Sonnet 4.6 | Vertex AI 経由、asia-northeast1 |
| DB | Firestore Native Mode | 無料枠で家庭規模は収まる |
| 認証 | Firebase Authentication | 家族メンバーホワイトリスト |
| 実行環境 | Cloud Run | `min-instances=0`、自動スケール |
| CI/CD | Cloud Build または GitHub Actions | どちらかに一本化 |
| 秘匿情報 | Secret Manager | API キー・サービスアカウント |
| ログ | Cloud Logging | ログベース指標で `guard_trigger_rate` など収集 |
| バックアップ | Cloud Storage | Cloud Scheduler で週次 export |

## 7. Phase 境界

### Phase 1: MVP

| 機能 | 採否 |
|---|---|
| 家族プロファイル CRUD | 採用 |
| 定番レシピ格納（100 品程度） | 採用 |
| 履歴記録（5 段階評価・メモ） | 採用 |
| 事前フィルタ（アレルゲン・直近 7 日） | 採用 |
| Sonnet 単体での夕食提案 | 採用 |
| 食材正規化辞書（28 品目＋家庭独自） | 採用 |
| 決定論的アレルゲン検証＋1 回リトライ | 採用 |
| Firebase Auth ログイン | 採用 |
| 提案 → 選択 → 直後フィードバック UI | 採用 |
| 提案ログ全保存 | 採用 |
| ゴールデンセット最小版＋評価 CLI | 採用 |
| Cloud Run デプロイ＋pre-commit＋CI | 採用 |
| Prompt Caching | 採用 |

### Phase A-E (2026-05): ダッシュボード化拡張

詳細プランは `.claude/plans/top-image-3-agile-corbato.md`。

| Phase | 機能 | 状態 |
|---|---|---|
| A | meal_plans コレクション・ダッシュボード骨格・週/月ビュー・日別詳細 | 完了 |
| B | 週間まとめて提案 (weekly_planner.py。2026-09 に骨子 + 日別詳細の分割経路へ一本化) | 完了 |
| C | shopping_lists コレクション・週次買い物リスト UI | 完了 |
| D | 家族プロファイル編集 (member CRUD + アレルゲン UI) | 完了 |
| E | エラーページ・ナビ整理・ドキュメント仕上げ | 完了 |

### Phase 2: 本運用

| 機能 | 内容 |
|---|---|
| 骨子・詳細の分担と並列実行 | 骨子 (effort=medium) → 日別の食材と作り方 (effort=low, 並列)。Haiku 分担は精度不足で見送り (docs/llm-integration.md §3.1) |
| 在庫管理 UI | pantry 入力・消費反映 |
| 事後フィードバック | 調理後の満足度収集 |
| Cloud Logging ダッシュボード | `guard_trigger_rate` 等の可視化 |

### Phase 3: 洗練

| 機能 | 内容 |
|---|---|
| 評価データセット拡充 | ゴールデンケース 30〜50 件 |
| プロンプト改善サイクル | 評価結果ベースの定期改善 |
| 朝食・昼食対応 | 食事区分を拡張 |

### Phase 4: 拡張

| 機能 | 内容 |
|---|---|
| Opus 4.7 による週間プランニング | 戦略レイヤー |
| 栄養バランス分析 | 週単位の栄養指標 |
| レシピ画像取り込み | Vision API 併用 |
| 家族メンバー別カスタマイズ | 個別向け提案 |

## 8. 関連ドキュメント

- [data-model.md](data-model.md) - Firestore データモデル
- [llm-integration.md](llm-integration.md) - LLM 統合仕様
- [guardrails.md](guardrails.md) - ガードレール詳細
- [evaluation.md](evaluation.md) - 評価・プロンプト版管理
- [operations.md](operations.md) - 運用設計
- [ui-spec.md](ui-spec.md) - UI 仕様
- [adr/](adr/) - アーキテクチャ意思決定

"""LLM 利用料金の表とコスト計算ユーティリティ.

モデル別の USD 単価 (1M トークン) を保持し、トークン数を受け取って
JPY コストを返す純関数を提供する.

価格は公式ドキュメントの値を反映する.
為替レート (USD->JPY) は変動するため、ここではハードコードせず呼出側から渡す
(`settings.usd_jpy_rate` を参照).

価格表に未登録のモデル (例: ``fake-sonnet``) は cost=0 として扱う.
本番集計から除外したい場合はモデル名で別途フィルタする運用とする.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)

# Anthropic API (api.anthropic.com 直接) はモデル ID に日付サフィックスが
# 付く場合がある (例: claude-sonnet-4-6-20260217). 価格表引きで取りこぼさない
# よう、末尾の日付サフィックスを除去して正規化する.
_DATE_SUFFIX_PATTERN = re.compile(r"-\d{8}$")


def normalize_model(model: str) -> str:
    """モデル ID 末尾の日付サフィックス (``-YYYYMMDD``) を除去する.

    サフィックスがなければそのまま返す.
    """
    return _DATE_SUFFIX_PATTERN.sub("", model)


@dataclass(frozen=True)
class ModelPricing:
    """1M トークン (= 1,000,000 tokens) あたりの USD 単価.

    cache_write は通常 input の 1.25 倍、cache_read は 0.1 倍が Anthropic 公式の標準比率.
    Vertex AI 経由でも同等価格 (2026 年時点).
    """

    input_usd_per_million: float
    output_usd_per_million: float
    cache_write_usd_per_million: float
    cache_read_usd_per_million: float


PRICING_TABLE: dict[str, ModelPricing] = {
    # Claude Sonnet 5. 2026-08-31 までは導入価格 (input 2 USD,
    # output 10 USD / 1M) が適用されるが、本テーブルには 2026-09-01 以降の
    # 通常価格を登録する. 導入価格期間中は実請求より過大に計上されるため、
    # 予算ガードは安全側 (早めに止まる方向) に倒れる.
    # Claude Opus 5.5 (anthropic プロバイダの既定モデル). Anthropic 公式:
    # input 4 USD, output 20 USD / 1M, cache read 0.20 USD. cache write は標準比率 1.25 倍.
    "claude-opus-5-5": ModelPricing(
        input_usd_per_million=4.0,
        output_usd_per_million=20.0,
        cache_write_usd_per_million=5.0,
        cache_read_usd_per_million=0.20,
    ),
    "claude-sonnet-5": ModelPricing(
        input_usd_per_million=3.0,
        output_usd_per_million=15.0,
        cache_write_usd_per_million=3.75,
        cache_read_usd_per_million=0.30,
    ),
    # Claude Haiku 4.5 (軽量経路). モデル ID にエイリアス指定すると API は
    # 日付付き ID (claude-haiku-4-5-20251001) を返すが、normalize_model が
    # 日付サフィックスを落とすため本エントリで引ける.
    "claude-haiku-4-5": ModelPricing(
        input_usd_per_million=1.0,
        output_usd_per_million=5.0,
        cache_write_usd_per_million=1.25,
        cache_read_usd_per_million=0.10,
    ),
    # Claude Sonnet 4.6. Anthropic 公式: input 3 USD, output 15 USD / 1M.
    # Vertex AI 経由でも同等価格 (2026 年時点) だが、本番投入前に
    # Cloud Console > Billing > Pricing の Anthropic models 単価を確認すること.
    # 差異があれば本テーブルを更新し、`memo/history/` に経緯を残す.
    "claude-sonnet-4-6": ModelPricing(
        input_usd_per_million=3.0,
        output_usd_per_million=15.0,
        cache_write_usd_per_million=3.75,
        cache_read_usd_per_million=0.30,
    ),
    # Gemini 2.5 Flash (Vertex AI). Claude クォータ未承認時の代替経路用.
    # implicit caching は書込課金なし、読み出しは入力単価の 25%.
    "gemini-2.5-flash": ModelPricing(
        input_usd_per_million=0.30,
        output_usd_per_million=2.50,
        cache_write_usd_per_million=0.0,
        cache_read_usd_per_million=0.075,
    ),
    # Gemini 2.5 Pro. 200K トークン以下のプロンプト帯の単価 (本システムの
    # プロンプトは十分小さいため超過帯の単価は未登録).
    "gemini-2.5-pro": ModelPricing(
        input_usd_per_million=1.25,
        output_usd_per_million=10.0,
        cache_write_usd_per_million=0.0,
        cache_read_usd_per_million=0.31,
    ),
}

# コスト計算で「未登録モデル」として扱っても警告を出さないモデル名.
# fake-sonnet はローカル開発用の Fake 実装、unknown は BudgetGuardedClient が
# LLM 呼び出し失敗時に記録する識別子 (llm/budget_guard.py 参照).
_ZERO_COST_MODELS = frozenset({"fake-sonnet", "unknown"})


_TOKENS_PER_MILLION = 1_000_000.0


def calculate_cost_jpy(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    usd_jpy_rate: float,
) -> float:
    """トークン数から JPY コストを算出する.

    Anthropic API では ``usage.input_tokens`` がキャッシュヒット分を含まない非キャッシュ
    入力トークンを返すため、4 種のトークンを独立した課金単位として加算する.

    未登録モデルは 0.0 を返す (Fake 実装などローカル開発で安全側に倒すため)。
    ただし fake-sonnet / unknown 以外の未登録モデルは、価格表への追加漏れ
    (課金の過小計上) に気づけるよう警告ログを出す.
    日付サフィックス付きモデル ID (Anthropic API 直接経路) は、素の ID で
    見つからない場合に正規化した ID で再度引く.
    """
    pricing = PRICING_TABLE.get(model) or PRICING_TABLE.get(normalize_model(model))
    if pricing is None:
        if model not in _ZERO_COST_MODELS:
            logger.warning("pricing.unknown_model", model=model)
        return 0.0
    usd = (
        input_tokens * pricing.input_usd_per_million
        + output_tokens * pricing.output_usd_per_million
        + cache_read_tokens * pricing.cache_read_usd_per_million
        + cache_write_tokens * pricing.cache_write_usd_per_million
    ) / _TOKENS_PER_MILLION
    return usd * usd_jpy_rate

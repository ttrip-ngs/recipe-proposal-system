"""LLM 料金計算 (`recipe_system.llm.pricing`) のテスト."""

from __future__ import annotations

import pytest

from recipe_system.llm.pricing import PRICING_TABLE, calculate_cost_jpy


def test_未登録モデルはコスト_0_を返す() -> None:
    """``fake-sonnet`` など Fake 実装のモデルはコスト計算対象外."""
    cost = calculate_cost_jpy(
        model="fake-sonnet",
        input_tokens=1000,
        output_tokens=1000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=150.0,
    )
    assert cost == 0.0


def test_全トークン_0_ならコスト_0() -> None:
    cost = calculate_cost_jpy(
        model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=150.0,
    )
    assert cost == 0.0


def test_input_1M_tokens_は_pricing_x_usd_jpy_rate() -> None:
    """1M input トークンで input_usd_per_million * usd_jpy_rate を返す."""
    pricing = PRICING_TABLE["claude-sonnet-4-6"]
    rate = 150.0
    cost = calculate_cost_jpy(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=rate,
    )
    expected = pricing.input_usd_per_million * rate
    assert cost == pytest.approx(expected, rel=1e-9)


def test_output_は_input_より高単価() -> None:
    """同じトークン数なら output の方がコストが高い (Claude 標準: input 3 / output 15)."""
    common = {
        "model": "claude-sonnet-4-6",
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "usd_jpy_rate": 150.0,
    }
    input_cost = calculate_cost_jpy(input_tokens=1_000_000, output_tokens=0, **common)
    output_cost = calculate_cost_jpy(input_tokens=0, output_tokens=1_000_000, **common)
    assert output_cost > input_cost


def test_cache_read_は_input_より安単価() -> None:
    """キャッシュヒット時の単価は通常 input より安い (Anthropic 公式比率)."""
    common = {
        "model": "claude-sonnet-4-6",
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "usd_jpy_rate": 150.0,
    }
    cache_read_cost = calculate_cost_jpy(input_tokens=0, cache_read_tokens=1_000_000, **common)
    input_cost = calculate_cost_jpy(input_tokens=1_000_000, cache_read_tokens=0, **common)
    assert cache_read_cost < input_cost


def test_cache_write_は_input_より高単価() -> None:
    """キャッシュ書込は通常 input の 1.25 倍ほど (Anthropic 公式比率)."""
    common = {
        "model": "claude-sonnet-4-6",
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "usd_jpy_rate": 150.0,
    }
    cache_write_cost = calculate_cost_jpy(input_tokens=0, cache_write_tokens=1_000_000, **common)
    input_cost = calculate_cost_jpy(input_tokens=1_000_000, cache_write_tokens=0, **common)
    assert cache_write_cost > input_cost


def test_gemini_2_5_flash_の単価でコスト計算される() -> None:
    pricing = PRICING_TABLE["gemini-2.5-flash"]
    rate = 150.0
    cost = calculate_cost_jpy(
        model="gemini-2.5-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=rate,
    )
    expected = (pricing.input_usd_per_million + pricing.output_usd_per_million) * rate
    assert cost == pytest.approx(expected, rel=1e-9)


def test_gemini_2_5_pro_の単価でコスト計算される() -> None:
    pricing = PRICING_TABLE["gemini-2.5-pro"]
    rate = 150.0
    cost = calculate_cost_jpy(
        model="gemini-2.5-pro",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=rate,
    )
    expected = pricing.input_usd_per_million * rate
    assert cost == pytest.approx(expected, rel=1e-9)


def test_claude_sonnet_5_の単価でコスト計算される() -> None:
    pricing = PRICING_TABLE["claude-sonnet-5"]
    rate = 150.0
    cost = calculate_cost_jpy(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=rate,
    )
    expected = (pricing.input_usd_per_million + pricing.output_usd_per_million) * rate
    assert cost == pytest.approx(expected, rel=1e-9)


def test_claude_haiku_4_5_はエイリアス指定でも日付付き_ID_でも同単価() -> None:
    """API は claude-haiku-4-5 指定でも日付付き ID を返すため、両方で引ける必要がある."""
    pricing = PRICING_TABLE["claude-haiku-4-5"]
    rate = 150.0
    common = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "usd_jpy_rate": rate,
    }
    expected = pricing.input_usd_per_million * rate
    assert calculate_cost_jpy(model="claude-haiku-4-5", **common) == pytest.approx(
        expected, rel=1e-9
    )
    assert calculate_cost_jpy(model="claude-haiku-4-5-20251001", **common) == pytest.approx(
        expected, rel=1e-9
    )


def test_haiku_4_5_は_sonnet_5_より安単価() -> None:
    """軽量経路として Haiku に落とす判断の前提 (同トークン数なら必ず安い)."""
    common = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "usd_jpy_rate": 150.0,
    }
    assert calculate_cost_jpy(model="claude-haiku-4-5", **common) < calculate_cost_jpy(
        model="claude-sonnet-5", **common
    )


def test_未知モデルは警告ログを出しつつコスト_0_を返す(monkeypatch: pytest.MonkeyPatch) -> None:
    """structlog は既定で stdlib logging を経由しないため caplog でなく直接モックする."""
    from recipe_system.llm import pricing

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pricing.logger, "warning", lambda event, **kwargs: calls.append({"event": event, **kwargs})
    )

    cost = calculate_cost_jpy(
        model="gemini-9.9-mystery",
        input_tokens=1000,
        output_tokens=1000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=150.0,
    )
    assert cost == 0.0
    assert calls == [{"event": "pricing.unknown_model", "model": "gemini-9.9-mystery"}]


@pytest.mark.parametrize("model", ["fake-sonnet", "unknown"])
def test_fake_sonnet_と_unknown_はコスト_0_かつ警告なし(
    model: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recipe_system.llm import pricing

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pricing.logger, "warning", lambda event, **kwargs: calls.append({"event": event, **kwargs})
    )

    cost = calculate_cost_jpy(
        model=model,
        input_tokens=1000,
        output_tokens=1000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=150.0,
    )
    assert cost == 0.0
    assert calls == []


def test_日付サフィックス付きモデルIDは正規化した単価で計算される() -> None:
    """Anthropic API 直接経路はモデル ID に日付サフィックスが付く場合がある."""
    pricing = PRICING_TABLE["claude-sonnet-4-6"]
    rate = 150.0
    cost = calculate_cost_jpy(
        model="claude-sonnet-4-6-20260217",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=rate,
    )
    expected = pricing.input_usd_per_million * rate
    assert cost == pytest.approx(expected, rel=1e-9)


def test_日付サフィックスがあっても未登録モデルはコスト_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """正規化しても価格表に無いモデルは従来どおり 0.0 + 警告ログ."""
    from recipe_system.llm import pricing

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pricing.logger, "warning", lambda event, **kwargs: calls.append({"event": event, **kwargs})
    )

    cost = calculate_cost_jpy(
        model="claude-mystery-9-9-20260217",
        input_tokens=1000,
        output_tokens=1000,
        cache_read_tokens=0,
        cache_write_tokens=0,
        usd_jpy_rate=150.0,
    )
    assert cost == 0.0
    assert calls == [{"event": "pricing.unknown_model", "model": "claude-mystery-9-9-20260217"}]


def test_4種のトークンが加算されてコスト計算される() -> None:
    """4 種のトークン費が単純加算される (相互作用なし) ことを確認."""
    pricing = PRICING_TABLE["claude-sonnet-4-6"]
    rate = 100.0
    cost = calculate_cost_jpy(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        usd_jpy_rate=rate,
    )
    expected_usd = (
        pricing.input_usd_per_million
        + pricing.output_usd_per_million
        + pricing.cache_read_usd_per_million
        + pricing.cache_write_usd_per_million
    )
    assert cost == pytest.approx(expected_usd * rate, rel=1e-9)

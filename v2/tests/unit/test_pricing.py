"""Tests for the LiteLLM-backed pricing module."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from arc.tui.pricing import PricingTable, format_cost


def _write_cache(path: Path, data: dict, *, age_seconds: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at_ts": int(time.time()) - age_seconds,
        "fetched_at": "2026-01-01",
        "data": data,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sample_data():
    return {
        "claude-haiku-4-5": {
            "input_cost_per_token": 0.0000008,
            "output_cost_per_token": 0.000004,
        },
        "gemini/gemini-3.1-flash-lite-preview": {
            "input_cost_per_token": 0.0000001,
            "output_cost_per_token": 0.0000004,
        },
    }


# ── lookup_for ─────────────────────────────────────────────────────────────


def test_lookup_hits_known_model_directly(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data())
    p = PricingTable(cache_path=cache)
    info = p.lookup_for(provider="anthropic", model="claude-haiku-4-5")
    assert info is not None
    assert info["input_cost_per_token"] == 0.0000008


def test_lookup_with_provider_prefix_variant(tmp_path):
    """Gemini models in LiteLLM use a 'gemini/' prefix; our config doesn't."""
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data())
    p = PricingTable(cache_path=cache)
    info = p.lookup_for(provider="gemini", model="gemini-3.1-flash-lite-preview")
    assert info is not None
    assert info["input_cost_per_token"] == 0.0000001


def test_lookup_unknown_model_returns_none(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data())
    p = PricingTable(cache_path=cache)
    info = p.lookup_for(provider="anthropic", model="model-that-doesnt-exist")
    assert info is None


def test_lookup_no_data_falls_back_to_static(tmp_path):
    """Cache missing AND fetch fails → a KNOWN model still resolves via the
    curated static fallback; an unknown model is still None."""
    cache = tmp_path / "missing.json"
    p = PricingTable(cache_path=cache)
    with patch("arc.tui.pricing.urlopen", side_effect=OSError("no net")):
        known = p.lookup_for(provider="anthropic", model="claude-haiku-4-5")
        unknown = p.lookup_for(provider="anthropic", model="model-that-doesnt-exist")
    assert known is not None and known["input_cost_per_token"] > 0
    assert unknown is None


def test_static_fallback_covers_new_gemini_models(tmp_path):
    """gemini-3.5-flash (too new for LiteLLM) resolves via the static table —
    this is what makes sub-agent cost non-zero."""
    p = PricingTable(cache_path=tmp_path / "missing.json")
    with patch("arc.tui.pricing.urlopen", side_effect=OSError("no net")):
        r = p.lookup_for(provider="gemini", model="gemini-3.5-flash")
    assert r is not None
    assert r["input_cost_per_token"] == pytest.approx(1.5 / 1e6)
    assert r["output_cost_per_token"] == pytest.approx(9 / 1e6)


# ── estimate_cost_usd ─────────────────────────────────────────────────────


def test_estimate_cost_uses_token_counts(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data())
    p = PricingTable(cache_path=cache)
    cost = p.estimate_cost_usd(
        provider="anthropic", model="claude-haiku-4-5",
        input_tokens=1_000_000, output_tokens=500_000,
    )
    # 1M × 0.0000008 + 500k × 0.000004 = 0.8 + 2.0 = 2.8
    assert cost == pytest.approx(2.8, rel=1e-6)


def test_estimate_cost_unknown_model_returns_none(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data())
    p = PricingTable(cache_path=cache)
    assert p.estimate_cost_usd(
        provider="anthropic", model="not-real",
        input_tokens=100, output_tokens=100,
    ) is None


# ── Cache freshness ───────────────────────────────────────────────────────


def test_fresh_cache_is_used_without_fetch(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data(), age_seconds=60)  # 1 min old
    p = PricingTable(cache_path=cache)
    with patch("arc.tui.pricing.urlopen") as mock_urlopen:
        info = p.lookup_for(provider="anthropic", model="claude-haiku-4-5")
    assert info is not None
    assert mock_urlopen.call_count == 0


def test_stale_cache_triggers_refetch(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data(), age_seconds=8 * 24 * 3600)  # 8 days old

    fresh_data = json.dumps({"claude-haiku-4-5": {
        "input_cost_per_token": 0.0000009,  # updated
        "output_cost_per_token": 0.000005,
    }}).encode("utf-8")

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return fresh_data

    with patch("arc.tui.pricing.urlopen", return_value=_FakeResp()) as mock_urlopen:
        p = PricingTable(cache_path=cache)
        info = p.lookup_for(provider="anthropic", model="claude-haiku-4-5")
    assert mock_urlopen.call_count == 1
    # Got the refreshed value
    assert info["input_cost_per_token"] == 0.0000009


def test_stale_cache_used_when_refetch_fails(tmp_path):
    cache = tmp_path / "cache.json"
    _write_cache(cache, _sample_data(), age_seconds=8 * 24 * 3600)

    p = PricingTable(cache_path=cache)
    with patch("arc.tui.pricing.urlopen", side_effect=OSError("net down")):
        info = p.lookup_for(provider="anthropic", model="claude-haiku-4-5")
    # Falls back to stale cache rather than returning None
    assert info is not None


# ── format_cost ───────────────────────────────────────────────────────────


def test_format_cost_none_returns_empty():
    assert format_cost(None) == ""


def test_format_cost_very_small_shows_4dp():
    assert format_cost(0.00012) == "$0.0001"
    assert format_cost(0.0023) == "$0.0023"


def test_format_cost_sub_dollar_shows_3dp():
    assert format_cost(0.123) == "$0.123"
    assert format_cost(0.9) == "$0.900"


def test_format_cost_dollar_plus_shows_2dp():
    assert format_cost(1.234) == "$1.23"
    assert format_cost(125.6) == "$125.60"

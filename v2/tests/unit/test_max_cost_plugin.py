"""Unit tests for the MaxCostPlugin (0019)."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from arc.plugins.max_cost import MaxCostExceeded, MaxCostPlugin


@dataclass
class _Session:
    provider_name: str
    provider_model: str


@dataclass
class _Ctx:
    session: _Session


def _table_with_rates(input_rate: float, output_rate: float):
    """Stub PricingTable that returns known rates regardless of model."""
    table = MagicMock()
    table.lookup_for = MagicMock(return_value={
        "input_cost_per_token": input_rate,
        "output_cost_per_token": output_rate,
    })
    table.estimate_cost_usd = MagicMock(return_value=0.0)
    return table


def _table_unknown():
    table = MagicMock()
    table.lookup_for = MagicMock(return_value=None)
    return table


def _resp(input_tokens: int, output_tokens: int):
    return MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)


# ── Accumulation ──────────────────────────────────────────────────────────


def test_running_total_accumulates_across_calls():
    plugin = MaxCostPlugin(max_cost_usd=10.0, pricing_table=_table_with_rates(0.0001, 0.0002))
    ctx = _Ctx(_Session("anthropic", "x"))
    plugin.after_llm_call(ctx, None, _resp(100, 50))
    plugin.after_llm_call(ctx, None, _resp(200, 100))
    # 100*0.0001 + 50*0.0002 = 0.02 ; 200*0.0001 + 100*0.0002 = 0.04 ; total 0.06
    assert plugin.running_usd == pytest.approx(0.06)


def test_cap_exceeded_raises_max_cost_exceeded():
    plugin = MaxCostPlugin(max_cost_usd=0.01, pricing_table=_table_with_rates(0.0001, 0.0002))
    ctx = _Ctx(_Session("anthropic", "x"))
    # 1000 input + 0 output → $0.10, well over $0.01 cap
    with pytest.raises(MaxCostExceeded, match=r"exceeded cap"):
        plugin.after_llm_call(ctx, None, _resp(1000, 0))


def test_cap_exact_does_not_raise():
    """Strictly > cap raises; exactly at cap doesn't."""
    plugin = MaxCostPlugin(max_cost_usd=0.1, pricing_table=_table_with_rates(0.0001, 0.0001))
    ctx = _Ctx(_Session("anthropic", "x"))
    # 500 input + 500 output = $0.10 exactly
    plugin.after_llm_call(ctx, None, _resp(500, 500))
    assert plugin.running_usd == pytest.approx(0.1)


def test_unknown_pricing_skips_enforcement(caplog):
    plugin = MaxCostPlugin(max_cost_usd=0.001, pricing_table=_table_unknown())
    ctx = _Ctx(_Session("ollama", "llama3.1:8b"))
    with caplog.at_level("WARNING", logger="arc.plugins.max_cost"):
        # Would normally trigger the cap, but unknown rate means no enforcement
        plugin.after_llm_call(ctx, None, _resp(10_000, 5_000))
    assert plugin.running_usd == 0.0
    # And a warning was logged exactly once for this (provider, model) pair
    assert any("no pricing data" in r.message for r in caplog.records)


def test_unknown_pricing_warning_emitted_once_per_pair(caplog):
    plugin = MaxCostPlugin(max_cost_usd=10.0, pricing_table=_table_unknown())
    ctx = _Ctx(_Session("ollama", "x"))
    with caplog.at_level("WARNING", logger="arc.plugins.max_cost"):
        plugin.after_llm_call(ctx, None, _resp(10, 10))
        plugin.after_llm_call(ctx, None, _resp(10, 10))
        plugin.after_llm_call(ctx, None, _resp(10, 10))
    warnings = [r for r in caplog.records if "no pricing data" in r.message]
    assert len(warnings) == 1


# ── Bus emission ──────────────────────────────────────────────────────────


def test_session_aborted_event_emitted_on_cap_breach():
    plugin = MaxCostPlugin(max_cost_usd=0.001, pricing_table=_table_with_rates(0.001, 0.001))
    bus = MagicMock()
    plugin.bind_bus(bus)

    ctx = _Ctx(_Session("anthropic", "claude-haiku-4-5"))
    with pytest.raises(MaxCostExceeded):
        plugin.after_llm_call(ctx, None, _resp(100, 100))

    assert bus.emit.called
    event = bus.emit.call_args.args[0]
    assert event.type == "session.aborted"
    assert event.payload["reason"] == "cost_cap"
    assert event.payload["provider"] == "anthropic"
    assert event.payload["cap_usd"] == 0.001
    assert event.payload["running_usd"] > 0.001


def test_no_emit_when_no_bus():
    """Plugin must not crash when emit-on-breach happens without a bus bound."""
    plugin = MaxCostPlugin(max_cost_usd=0.001, pricing_table=_table_with_rates(0.001, 0.001))
    ctx = _Ctx(_Session("anthropic", "x"))
    with pytest.raises(MaxCostExceeded):
        plugin.after_llm_call(ctx, None, _resp(100, 100))

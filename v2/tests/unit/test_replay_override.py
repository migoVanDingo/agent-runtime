"""Unit tests for replay override + target parsing (0019)."""
from __future__ import annotations

import pytest

from arc.config import Config, ProviderConfig, RetryConfig
from arc.replay.override import (
    OverrideError,
    apply_override,
    known_providers,
    parse_target,
    parse_target_list,
)


def _minimal_config(provider_name: str = "gemini", model: str = "gemini-2.5-flash") -> Config:
    from arc.config import (
        BootstrapConfig,
        PluginsConfig,
        RuntimeConfig,
        ToolsConfig,
        TUIConfig,
    )
    from pathlib import Path

    return Config(
        runtime=RuntimeConfig(
            workspace=".", max_iterations=50, max_tool_calls_per_turn=30,
            show_thinking=True, log_level="info", system_prompt="",
            iteration_cap_message="", tool_call_cap_message="",
            cycle_detection_threshold=5, cycle_detected_message="",
        ),
        provider=ProviderConfig(
            name=provider_name, model=model,
            api_key_env=("GEMINI_API_KEY" if provider_name == "gemini" else "ANTHROPIC_API_KEY"),
            base_url=None, timeout_seconds=60.0,
            retry=RetryConfig(max_attempts=3, backoff_base_seconds=2, backoff_max_seconds=32),
            params={"temperature": 0, "max_tokens": 256},
        ),
        tools=ToolsConfig(enabled=["ls"], config={}),
        plugins=PluginsConfig(
            failure_threshold=3, exception_message_max_chars=200, enabled=[],
        ),
        tui=TUIConfig(
            enabled=True, theme="default", inline_mode=True, spinner_style="dots",
            prompt_prefix="> ", show_token_counts=True, show_event_count=True,
            show_thinking=True, tool_output_max_lines=30,
            toolbar_enabled=True, input_history_enabled=True,
        ),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=Path("/tmp/config.yml"),
    )


# ── apply_override ─────────────────────────────────────────────────────────


def test_apply_override_swaps_provider_and_model():
    cfg = _minimal_config()
    out = apply_override(cfg, provider="ollama", model="llama3.1:8b")
    assert out.provider.name == "ollama"
    assert out.provider.model == "llama3.1:8b"
    assert out.provider.api_key_env == "OLLAMA_API_KEY"
    assert out.provider.base_url == "http://localhost:11434/v1"


def test_apply_override_keeps_retry_and_params():
    cfg = _minimal_config()
    out = apply_override(cfg, provider="anthropic", model="claude-haiku-4-5")
    assert out.provider.retry == cfg.provider.retry
    assert out.provider.params == cfg.provider.params


def test_apply_override_cloud_provider_sets_null_base_url():
    cfg = _minimal_config()
    out = apply_override(cfg, provider="anthropic", model="claude-haiku-4-5")
    assert out.provider.base_url is None
    assert out.provider.api_key_env == "ANTHROPIC_API_KEY"


def test_apply_override_unknown_provider_raises():
    cfg = _minimal_config()
    with pytest.raises(OverrideError, match="unknown provider"):
        apply_override(cfg, provider="bogus", model="x")


def test_apply_override_leaves_other_sections_alone():
    cfg = _minimal_config()
    out = apply_override(cfg, provider="ollama", model="m")
    assert out.tools == cfg.tools
    assert out.plugins == cfg.plugins
    assert out.runtime == cfg.runtime


def test_known_providers_covers_all_four():
    names = known_providers()
    assert set(names) == {"anthropic", "gemini", "ollama", "llama_cpp"}


# ── parse_target ──────────────────────────────────────────────────────────


def test_parse_target_simple_pair():
    assert parse_target("anthropic:claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")


def test_parse_target_handles_colon_in_model_name():
    """Ollama tags contain colons (`llama3.1:8b`). Split on the FIRST colon only."""
    assert parse_target("ollama:llama3.1:8b") == ("ollama", "llama3.1:8b")


def test_parse_target_missing_colon_raises():
    with pytest.raises(OverrideError, match="expected 'provider:model'"):
        parse_target("anthropic claude-haiku-4-5")


def test_parse_target_unknown_provider_raises():
    with pytest.raises(OverrideError, match="unknown provider"):
        parse_target("bogus:x")


def test_parse_target_empty_model_raises():
    with pytest.raises(OverrideError, match="empty provider or model"):
        parse_target("anthropic:")


# ── parse_target_list ─────────────────────────────────────────────────────


def test_parse_target_list_two_pairs():
    out = parse_target_list("anthropic:claude-haiku-4-5,ollama:llama3.1:8b")
    assert out == [("anthropic", "claude-haiku-4-5"), ("ollama", "llama3.1:8b")]


def test_parse_target_list_tolerates_whitespace():
    out = parse_target_list(" gemini:gemini-2.5-pro ,  ollama:m ")
    assert out == [("gemini", "gemini-2.5-pro"), ("ollama", "m")]


def test_parse_target_list_empty_raises():
    with pytest.raises(OverrideError, match="empty target list"):
        parse_target_list(", ,")


def test_parse_target_list_propagates_bad_entry():
    with pytest.raises(OverrideError, match="unknown provider"):
        parse_target_list("anthropic:x,bogus:y")

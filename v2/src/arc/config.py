"""Config loading and validation.

The config is the single source of truth for every user-tunable value (per the
"no-hardcoded-defaults" principle in _design/0001-foundation-phase0-design.md
§3). The shape of the YAML is in §8.1; validation rules in §8.3.

Design choices:
  - Dataclasses, not pydantic. Keeps deps minimal and validation explicit.
  - Mutable load → frozen dataclasses → validated Config. The raw dict is
    discarded after parsing so nothing downstream can mutate it.
  - Unknown top-level keys are errors; unknown sub-keys are warnings (lets
    us add experimental keys without breaking older configs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── Exceptions ──────────────────────────────────────────────────────────────

class ConfigError(Exception):
    """Raised when config is missing, malformed, or fails validation.

    The message is meant to be shown to the user verbatim — be specific
    about what's wrong and how to fix it.
    """


# ── Dataclasses (mirror config.yml structure) ───────────────────────────────

@dataclass(frozen=True)
class RuntimeConfig:
    workspace: str
    max_iterations: int
    max_tool_calls_per_turn: int
    show_thinking: bool
    log_level: str
    system_prompt: str
    iteration_cap_message: str
    tool_call_cap_message: str
    cycle_detection_threshold: int
    cycle_detected_message: str


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key_env: str
    base_url: str | None
    timeout_seconds: float
    retry: RetryConfig
    params: dict[str, Any]


@dataclass(frozen=True)
class ToolsConfig:
    enabled: list[str]
    config: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class PluginEntry:
    name: str
    enabled: bool
    config: dict[str, Any]
    hooks_order: dict[str, int]


@dataclass(frozen=True)
class PluginsConfig:
    failure_threshold: int
    exception_message_max_chars: int
    enabled: list[PluginEntry]

    def active(self) -> list[PluginEntry]:
        """Only the plugins with enabled=True. Skip flag-disabled."""
        return [p for p in self.enabled if p.enabled]


@dataclass(frozen=True)
class TUIConfig:
    enabled: bool
    theme: str
    inline_mode: bool
    spinner_style: str
    prompt_prefix: str
    show_token_counts: bool
    show_event_count: bool
    show_thinking: bool
    tool_output_max_lines: int
    toolbar_enabled: bool
    input_history_enabled: bool
    subagent_activity: bool = True


@dataclass(frozen=True)
class BootstrapConfig:
    create_workspace_dir: bool
    write_example_session: bool


@dataclass(frozen=True)
class SubAgentEntry:
    """One entry from the `subagents:` config block.

    Can either OVERRIDE fields of an existing plugin/builtin spec (only the
    listed fields are touched) or DEFINE a new config-only spec (must carry
    description, provider, model, system_prompt at minimum).

    `enabled` defaults to True so listing an entry is equivalent to turning
    it on, mirroring the `plugins.enabled` pattern.
    """
    name: str
    enabled: bool
    fields: dict[str, Any]            # all override/definition fields except `enabled`


@dataclass(frozen=True)
class SubAgentsConfig:
    """Parsed `subagents:` block. The registry consumes `as_overrides()`."""
    entries: list[SubAgentEntry]

    def as_overrides(self) -> dict[str, dict[str, Any]]:
        """Shape the registry expects: name -> {**fields, "enabled": bool}."""
        out: dict[str, dict[str, Any]] = {}
        for e in self.entries:
            row = dict(e.fields)
            row["enabled"] = e.enabled
            out[e.name] = row
        return out


@dataclass(frozen=True)
class Config:
    """Resolved, validated config. Frozen — nothing mutates after load."""
    runtime: RuntimeConfig
    provider: ProviderConfig
    tools: ToolsConfig
    plugins: PluginsConfig
    tui: TUIConfig
    bootstrap: BootstrapConfig
    source_path: Path  # where the config was loaded from (debug aid)
    # Sub-agents (0020). Optional with empty default so older callers
    # (tests, programmatic Config construction) don't have to thread it.
    subagents: SubAgentsConfig = field(default_factory=lambda: SubAgentsConfig(entries=[]))


# ── Loader ──────────────────────────────────────────────────────────────────

# `subagents` is optional — older configs without the block keep loading.
KNOWN_TOP_LEVEL = {"runtime", "provider", "tools", "plugins", "subagents", "tui", "bootstrap"}
_REQUIRED_TOP_LEVEL = {"runtime", "provider", "tools", "plugins", "tui", "bootstrap"}


def load(path: Path) -> Config:
    """Load config from a YAML file. Raises ConfigError on any problem.

    Validation per design §8.3:
      - File must exist (use `arc bootstrap` to create one)
      - Must parse as YAML
      - Top-level keys must all be known
      - Required sections must be present (everything in KNOWN_TOP_LEVEL)
    """
    if not path.exists():
        raise ConfigError(
            f"config not found at {path}\n"
            f"  run `arc bootstrap` to create a default config"
        )

    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise ConfigError(f"config at {path} is not valid YAML:\n  {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"config at {path} must be a YAML mapping at the top level")

    # Validate top-level keys
    unknown = set(raw.keys()) - KNOWN_TOP_LEVEL
    if unknown:
        raise ConfigError(
            f"config at {path} has unknown top-level keys: {sorted(unknown)}\n"
            f"  known keys: {sorted(KNOWN_TOP_LEVEL)}"
        )
    missing = _REQUIRED_TOP_LEVEL - set(raw.keys())
    if missing:
        raise ConfigError(
            f"config at {path} is missing required sections: {sorted(missing)}"
        )

    try:
        return Config(
            runtime=_parse_runtime(raw["runtime"]),
            provider=_parse_provider(raw["provider"]),
            tools=_parse_tools(raw["tools"]),
            plugins=_parse_plugins(raw["plugins"]),
            subagents=_parse_subagents(raw.get("subagents")),
            tui=_parse_tui(raw["tui"]),
            bootstrap=_parse_bootstrap(raw["bootstrap"]),
            source_path=path,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"config at {path} failed validation:\n  {e}") from e


# ── Section parsers ─────────────────────────────────────────────────────────

def _parse_runtime(d: dict) -> RuntimeConfig:
    _require(d, "runtime", ["workspace", "max_iterations", "max_tool_calls_per_turn",
                            "show_thinking", "log_level", "system_prompt",
                            "iteration_cap_message", "tool_call_cap_message",
                            "cycle_detection_threshold", "cycle_detected_message"])
    return RuntimeConfig(
        workspace=str(d["workspace"]),
        max_iterations=int(d["max_iterations"]),
        max_tool_calls_per_turn=int(d["max_tool_calls_per_turn"]),
        show_thinking=bool(d["show_thinking"]),
        log_level=str(d["log_level"]),
        system_prompt=str(d["system_prompt"]),
        iteration_cap_message=str(d["iteration_cap_message"]),
        tool_call_cap_message=str(d["tool_call_cap_message"]),
        cycle_detection_threshold=int(d["cycle_detection_threshold"]),
        cycle_detected_message=str(d["cycle_detected_message"]),
    )


def _parse_provider(d: dict) -> ProviderConfig:
    _require(d, "provider", ["name", "model", "api_key_env", "timeout_seconds",
                             "retry", "params"])
    retry_d = d["retry"]
    _require(retry_d, "provider.retry", ["max_attempts", "backoff_base_seconds",
                                          "backoff_max_seconds"])
    return ProviderConfig(
        name=str(d["name"]),
        model=str(d["model"]),
        api_key_env=str(d["api_key_env"]),
        base_url=d.get("base_url"),  # may be null
        timeout_seconds=float(d["timeout_seconds"]),
        retry=RetryConfig(
            max_attempts=int(retry_d["max_attempts"]),
            backoff_base_seconds=float(retry_d["backoff_base_seconds"]),
            backoff_max_seconds=float(retry_d["backoff_max_seconds"]),
        ),
        params=dict(d["params"] or {}),
    )


def _parse_tools(d: dict) -> ToolsConfig:
    _require(d, "tools", ["enabled", "config"])
    enabled = d["enabled"] or []
    if not isinstance(enabled, list):
        raise ValueError("tools.enabled must be a list of tool names")
    return ToolsConfig(
        enabled=[str(x) for x in enabled],
        config={str(k): dict(v or {}) for k, v in (d["config"] or {}).items()},
    )


def _parse_plugins(d: dict) -> PluginsConfig:
    _require(d, "plugins", ["failure_threshold", "exception_message_max_chars", "enabled"])
    entries_raw = d["enabled"] or []
    if not isinstance(entries_raw, list):
        raise ValueError("plugins.enabled must be a list of plugin entries")
    entries = []
    for i, e in enumerate(entries_raw):
        if not isinstance(e, dict):
            raise ValueError(f"plugins.enabled[{i}] must be a mapping")
        if "name" not in e:
            raise ValueError(f"plugins.enabled[{i}] missing 'name'")
        entries.append(PluginEntry(
            name=str(e["name"]),
            # default enabled=True so listing a plugin = turning it on, opt out
            # explicitly with enabled: false
            enabled=bool(e.get("enabled", True)),
            config=dict(e.get("config") or {}),
            hooks_order={str(k): int(v) for k, v in (e.get("hooks_order") or {}).items()},
        ))
    return PluginsConfig(
        failure_threshold=int(d["failure_threshold"]),
        exception_message_max_chars=int(d["exception_message_max_chars"]),
        enabled=entries,
    )


def _parse_subagents(d: dict | None) -> SubAgentsConfig:
    """Parse the optional `subagents:` block.

    Shape:

        subagents:
          example_log_grepper:               # override-only block
            model: claude-haiku-4-5
            timeout_s: 60
          custom_classifier:                 # new spec definition
            description: ...
            provider: anthropic
            model: claude-haiku-4-5
            system_prompt: ...
            tools: [bash]
            enabled: true

    The registry distinguishes overrides from new specs by whether the
    underlying name is already discovered. Here we just parse the YAML
    into SubAgentEntry instances; validation of "is this a valid override
    vs. a complete new spec?" happens at registry.discover() time so we
    can give per-spec errors with full discovery context.
    """
    if d is None:
        return SubAgentsConfig(entries=[])
    if not isinstance(d, dict):
        raise ValueError("subagents must be a mapping of spec_name -> fields")
    entries: list[SubAgentEntry] = []
    for name, raw in d.items():
        if not isinstance(raw, dict):
            raise ValueError(
                f"subagents.{name} must be a mapping; got {type(raw).__name__}"
            )
        # Pull `enabled` out; everything else becomes a field override / definition.
        enabled = bool(raw.get("enabled", True))
        fields = {k: v for k, v in raw.items() if k != "enabled"}
        entries.append(SubAgentEntry(
            name=str(name),
            enabled=enabled,
            fields=fields,
        ))
    return SubAgentsConfig(entries=entries)


def _parse_tui(d: dict) -> TUIConfig:
    # Original keys are required; newer keys (added in 0011-tui-polish) fall
    # back to defaults so older configs keep loading without a re-bootstrap.
    _require(d, "tui", ["enabled", "theme", "inline_mode", "spinner_style",
                        "prompt_prefix", "show_token_counts", "show_event_count"])
    return TUIConfig(
        enabled=bool(d["enabled"]),
        theme=str(d["theme"]),
        inline_mode=bool(d["inline_mode"]),
        spinner_style=str(d["spinner_style"]),
        prompt_prefix=str(d["prompt_prefix"]),
        show_token_counts=bool(d["show_token_counts"]),
        show_event_count=bool(d["show_event_count"]),
        show_thinking=bool(d.get("show_thinking", True)),
        tool_output_max_lines=int(d.get("tool_output_max_lines", 30)),
        toolbar_enabled=bool(d.get("toolbar_enabled", True)),
        input_history_enabled=bool(d.get("input_history_enabled", True)),
        subagent_activity=bool(d.get("subagent_activity", True)),
    )


def _parse_bootstrap(d: dict) -> BootstrapConfig:
    _require(d, "bootstrap", ["create_workspace_dir", "write_example_session"])
    return BootstrapConfig(
        create_workspace_dir=bool(d["create_workspace_dir"]),
        write_example_session=bool(d["write_example_session"]),
    )


def _require(d: dict, section: str, keys: list[str]) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"section '{section}' missing required keys: {missing}")

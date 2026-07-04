"""Comment-preserving config.yml mutation for `arc setup`.

PyYAML drops comments on round-trip; we use ruamel.yaml in round-trip
mode so the picker can edit `provider.name`, `provider.model`,
`provider.base_url`, and `provider.api_key_env` without nuking the
extensive commented examples in the shipped default file.

See _design/0017-provider-picker.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path


# api_key_env values shipped with the default catalog.  If the existing
# config has any of these, the picker is free to overwrite — they're not
# user customizations.
_KNOWN_API_KEY_ENVS = frozenset({
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OLLAMA_API_KEY",
    "LLAMA_CPP_API_KEY",
    "OPENAI_API_KEY",  # future-proofing for when OpenAI provider lands
})


@dataclass(frozen=True)
class WriteChange:
    """One field that the writer mutated (or left alone), reported back
    to the caller so the picker can render a diff to the user."""
    key: str             # e.g. "provider.name"
    old: str | None
    new: str | None
    skipped: bool = False
    skip_reason: str = ""  # populated when skipped=True


def write_provider_choice(
    config_path: Path,
    *,
    name: str,
    model: str,
    base_url: str | None,
    api_key_env: str,
) -> list[WriteChange]:
    """Mutate the `provider:` block of an existing config.yml.

    Rules:
      - `name` and `model` are always set (the whole point of running setup).
      - `base_url` and `api_key_env` are set ONLY if they're currently
        null/empty/missing.  Honoring an explicit non-null value the user
        already set is the right default — they had a reason.

    Returns one WriteChange per field touched (or skipped).  Empty list
    means no diff at all.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")            # round-trip mode preserves comments + order
    yaml.preserve_quotes = True
    yaml.width = 4096                # don't reflow long lines

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None or "provider" not in data:
        raise ValueError(
            f"config at {config_path} has no `provider:` block; can't safely "
            f"set provider/model.  Run `arc bootstrap --force` to recreate it."
        )

    prov = data["provider"]
    changes: list[WriteChange] = []

    # name and model are always written.
    changes.append(_set_always(prov, "name", name, key_label="provider.name"))
    changes.append(_set_always(prov, "model", model, key_label="provider.model"))

    # base_url preserves existing non-null/non-empty values.
    changes.append(_set_if_missing(
        prov, "base_url", base_url, key_label="provider.base_url",
    ))
    # api_key_env preserves *custom* values but overwrites known-default
    # env-var names from other providers — otherwise switching from gemini
    # → anthropic via the picker leaves you with GEMINI_API_KEY and a
    # config that can't load.
    changes.append(_set_overwriting_known_defaults(
        prov, "api_key_env", api_key_env, key_label="provider.api_key_env",
        known_defaults=_KNOWN_API_KEY_ENVS,
    ))

    # Dump back to disk.  Use StringIO so we only write if dump succeeds.
    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")

    return changes


def write_plugin_enablement(
    config_path: Path,
    *,
    name: str,
    enabled: bool,
    config: dict | None = None,
    hooks_order: dict[str, int] | None = None,
) -> list[WriteChange]:
    """Append or update a plugin entry under `plugins.enabled` in config.yml.

    Used by:
      - the first-run enablement prompt (after `pip install arc-plugin-*`)
      - `arc plugins enable|disable` toggles

    Rules:
      - If an entry with `name` already exists, only `enabled` is updated.
        Existing `config` and `hooks_order` are preserved (the user may have
        customized them).
      - If no entry exists, a new one is appended with the provided defaults.
      - Comments and ordering elsewhere in the file are preserved (ruamel
        round-trip).

    Returns one WriteChange per mutation (or skip).
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None or "plugins" not in data:
        raise ValueError(
            f"config at {config_path} has no `plugins:` block; can't safely "
            f"update plugin enablement. Run `arc bootstrap --force` to recreate it."
        )

    plugins_block = data["plugins"]
    if "enabled" not in plugins_block or plugins_block["enabled"] is None:
        plugins_block["enabled"] = CommentedSeq()
    enabled_list = plugins_block["enabled"]

    changes: list[WriteChange] = []
    found_idx = None
    for i, entry in enumerate(enabled_list):
        if isinstance(entry, dict) and entry.get("name") == name:
            found_idx = i
            break

    if found_idx is not None:
        entry = enabled_list[found_idx]
        old_enabled = bool(entry.get("enabled", True))
        if old_enabled != enabled:
            entry["enabled"] = enabled
            changes.append(WriteChange(
                key=f"plugins.enabled[{name}].enabled",
                old=str(old_enabled),
                new=str(enabled),
            ))
        else:
            changes.append(WriteChange(
                key=f"plugins.enabled[{name}].enabled",
                old=str(old_enabled),
                new=str(enabled),
            ))
    else:
        new_entry = CommentedMap()
        new_entry["name"] = name
        new_entry["enabled"] = enabled
        new_entry["config"] = CommentedMap(config or {})
        new_entry["hooks_order"] = CommentedMap(hooks_order or {})
        enabled_list.append(new_entry)
        changes.append(WriteChange(
            key=f"plugins.enabled[{name}]",
            old=None,
            new=f"{{enabled: {enabled}}}",
        ))

    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")

    return changes


def write_mcp_server_enablement(
    config_path: Path,
    *,
    server: str,
    enabled: bool,
) -> list[WriteChange]:
    """Toggle `plugins.enabled[mcp].config.servers[<server>].enabled` in place.

    MCP is a built-in plugin (see _deviations/0001); its servers are nested in
    the plugin's config block. This flips one server's flag, preserving comments
    and everything else (ruamel round-trip). Used by `arc mcp` and the setup hub
    MCP section.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    enabled_list = (data or {}).get("plugins", {}).get("enabled")
    mcp_entry = None
    for entry in enabled_list or []:
        if isinstance(entry, dict) and entry.get("name") == "mcp":
            mcp_entry = entry
            break
    if mcp_entry is None:
        raise ValueError(
            "no `mcp` plugin entry in config.yml; enable it via `arc plugins` first"
        )
    servers = (mcp_entry.get("config") or {}).get("servers")
    srv = None
    for s in servers or []:
        if isinstance(s, dict) and s.get("name") == server:
            srv = s
            break
    if srv is None:
        raise ValueError(f"no mcp server named {server!r} under plugins.enabled[mcp].config.servers")

    old = bool(srv.get("enabled", True))
    srv["enabled"] = enabled
    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")
    return [WriteChange(
        key=f"plugins.enabled[mcp].config.servers[{server}].enabled",
        old=str(old),
        new=str(enabled),
    )]


def write_mcp_server_add(
    config_path: Path,
    *,
    name: str,
    transport: str,
    url: str | None = None,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    tool_prefix: str | None = None,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
    enabled: bool = True,
) -> list[WriteChange]:
    """Add (or update) an MCP server under `plugins.enabled[mcp].config.servers`.

    The programmatic entry point for registering an MCP server — used by
    `arc mcp add` and callable directly (e.g. a service self-registering after
    it starts). Upsert semantics: an existing server with `name` is replaced.
    Creates the `mcp` plugin entry itself if the config doesn't have one yet
    (older configs). Comment-preserving (ruamel round-trip).

    Validates the resulting server spec before writing; raises McpConfigError on
    a bad shape (unknown transport, http without url, stdio without command).
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    from arc.mcp.config import parse_mcp_config

    # Validate the spec up front so we never write a broken server entry.
    spec: dict = {"name": name, "transport": transport, "enabled": enabled}
    if url is not None:
        spec["url"] = url
    if command is not None:
        spec["command"] = list(command)
    if env:
        spec["env"] = dict(env)
    if cwd is not None:
        spec["cwd"] = cwd
    if tool_prefix is not None:  # "" is meaningful: no prefix
        spec["tool_prefix"] = tool_prefix
    if tools_allow:
        spec["tools_allow"] = list(tools_allow)
    if tools_deny:
        spec["tools_deny"] = list(tools_deny)
    parse_mcp_config({"servers": [spec]})  # raises McpConfigError if invalid

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None or "plugins" not in data:
        raise ValueError(
            f"config at {config_path} has no `plugins:` block; run `arc bootstrap --force`"
        )

    plugins_block = data["plugins"]
    if not plugins_block.get("enabled"):
        plugins_block["enabled"] = CommentedSeq()
    enabled_list = plugins_block["enabled"]

    changes: list[WriteChange] = []
    mcp_entry = None
    for entry in enabled_list:
        if isinstance(entry, dict) and entry.get("name") == "mcp":
            mcp_entry = entry
            break
    if mcp_entry is None:
        mcp_entry = CommentedMap()
        mcp_entry["name"] = "mcp"
        cfg_map = CommentedMap()
        cfg_map["failure_threshold"] = 3
        cfg_map["call_timeout_s"] = 30
        cfg_map["servers"] = CommentedSeq()
        mcp_entry["config"] = cfg_map
        ho = CommentedMap()
        ho["on_session_start"] = 8
        ho["on_session_end"] = 8
        mcp_entry["hooks_order"] = ho
        enabled_list.append(mcp_entry)
        changes.append(WriteChange(key="plugins.enabled[mcp]", old=None, new="created"))

    cfg_block = mcp_entry.get("config")
    if cfg_block is None:
        cfg_block = mcp_entry["config"] = CommentedMap()
    if cfg_block.get("servers") is None:
        cfg_block["servers"] = CommentedSeq()
    servers = cfg_block["servers"]

    srv = CommentedMap()
    srv["name"] = name
    srv["transport"] = transport
    srv["enabled"] = enabled
    if transport == "http":
        srv["url"] = url
    if transport == "stdio":
        srv["command"] = CommentedSeq(command or [])
        if env:
            srv["env"] = CommentedMap(env)
        if cwd:
            srv["cwd"] = cwd
    if tool_prefix is not None:  # "" is meaningful: no prefix
        srv["tool_prefix"] = tool_prefix
    if tools_allow:
        srv["tools_allow"] = CommentedSeq(tools_allow)
    if tools_deny:
        srv["tools_deny"] = CommentedSeq(tools_deny)

    idx = next((i for i, s in enumerate(servers)
                if isinstance(s, dict) and s.get("name") == name), None)
    if idx is None:
        servers.append(srv)
        changes.append(WriteChange(
            key=f"plugins.enabled[mcp].config.servers[{name}]", old=None, new=transport))
    else:
        servers[idx] = srv
        changes.append(WriteChange(
            key=f"plugins.enabled[mcp].config.servers[{name}]",
            old="(existing)", new=f"{transport} (updated)"))

    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")
    return changes


def write_mcp_server_remove(config_path: Path, *, name: str) -> list[WriteChange]:
    """Remove an MCP server from `plugins.enabled[mcp].config.servers`."""
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    enabled_list = (data or {}).get("plugins", {}).get("enabled") or []
    mcp_entry = next((e for e in enabled_list
                      if isinstance(e, dict) and e.get("name") == "mcp"), None)
    if mcp_entry is None:
        raise ValueError("no `mcp` plugin entry in config.yml")
    servers = (mcp_entry.get("config") or {}).get("servers") or []
    idx = next((i for i, s in enumerate(servers)
                if isinstance(s, dict) and s.get("name") == name), None)
    if idx is None:
        raise ValueError(f"no mcp server named {name!r}")
    del servers[idx]

    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")
    return [WriteChange(
        key=f"plugins.enabled[mcp].config.servers[{name}]", old="present", new="removed")]


def write_subagent_enablement(
    config_path: Path,
    *,
    name: str,
    enabled: bool,
) -> list[WriteChange]:
    """Toggle `subagents.<name>.enabled` in config.yml, preserving comments.

    If the spec has no existing block, a fresh one is inserted with just
    `enabled: <bool>`. Field overrides (model, timeout_s, etc.) are not
    touched by this helper — use a `subagents:` block edit for those.

    Used by `arc subagents enable|disable`.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None:
        raise ValueError(f"config at {config_path} is empty")
    if "subagents" not in data or data["subagents"] is None:
        data["subagents"] = CommentedMap()

    block = data["subagents"]
    if name not in block:
        new_entry = CommentedMap()
        new_entry["enabled"] = enabled
        block[name] = new_entry
        changes = [WriteChange(
            key=f"subagents.{name}",
            old=None,
            new=f"{{enabled: {enabled}}}",
        )]
    else:
        entry = block[name]
        old_enabled = bool(entry.get("enabled", True))
        entry["enabled"] = enabled
        changes = [WriteChange(
            key=f"subagents.{name}.enabled",
            old=str(old_enabled),
            new=str(enabled),
        )]

    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")
    return changes


def remove_plugin_entry(config_path: Path, *, name: str) -> list[WriteChange]:
    """Remove a plugin entry by name. Used by `arc plugins` when cleaning up
    dangling entries (plugin uninstalled but still listed in config.yml).

    Returns [WriteChange(...)] if removed, [] if no entry by that name.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if (
        data is None
        or "plugins" not in data
        or "enabled" not in data["plugins"]
        or data["plugins"]["enabled"] is None
    ):
        return []

    enabled_list = data["plugins"]["enabled"]
    for i, entry in enumerate(enabled_list):
        if isinstance(entry, dict) and entry.get("name") == name:
            del enabled_list[i]
            buf = StringIO()
            yaml.dump(data, buf)
            config_path.write_text(buf.getvalue(), encoding="utf-8")
            return [WriteChange(
                key=f"plugins.enabled[{name}]",
                old="present", new=None,
            )]
    return []


def render_changes(changes: list[WriteChange]) -> str:
    """Human-readable diff string for the picker's success screen."""
    lines: list[str] = []
    for c in changes:
        if c.skipped:
            lines.append(f"  - {c.key}: kept existing value ({c.old!r})  [{c.skip_reason}]")
            continue
        if c.old == c.new:
            lines.append(f"  - {c.key}: unchanged ({c.new!r})")
        elif c.old is None:
            lines.append(f"  + {c.key} = {c.new!r}")
        else:
            lines.append(f"  ~ {c.key}: {c.old!r} → {c.new!r}")
    return "\n".join(lines) if lines else "  (no changes)"


# ── Field-set helpers ──────────────────────────────────────────────────────


def _set_always(prov, field: str, value, *, key_label: str) -> WriteChange:
    old = prov.get(field)
    old_str = None if old is None else str(old)
    new_str = None if value is None else str(value)
    if old == value:
        return WriteChange(key=key_label, old=old_str, new=new_str)
    prov[field] = value
    return WriteChange(key=key_label, old=old_str, new=new_str)


def _set_overwriting_known_defaults(
    prov, field: str, value, *, key_label: str, known_defaults: frozenset[str],
) -> WriteChange:
    """Set if missing/null/empty OR if the existing value is in known_defaults.

    Preserves user customizations (anything not in the known-defaults set).
    """
    existing = prov.get(field, None)
    old_str = None if existing is None else str(existing)
    new_str = None if value is None else str(value)

    if existing in (None, ""):
        if value not in (None, ""):
            prov[field] = value
        return WriteChange(key=key_label, old=old_str, new=new_str)

    if str(existing) in known_defaults:
        if existing != value:
            prov[field] = value
        return WriteChange(key=key_label, old=old_str, new=new_str)

    # Looks like a real user customization — preserve it.
    return WriteChange(
        key=key_label, old=old_str, new=new_str,
        skipped=True, skip_reason="user value preserved",
    )


def _set_if_missing(prov, field: str, value, *, key_label: str) -> WriteChange:
    """Write `value` to `prov[field]` only if the field is missing, null,
    or an empty string.  Otherwise leave the user's value alone and
    record a 'skipped' change."""
    existing = prov.get(field, None)
    has_real_value = existing not in (None, "")
    old_str = None if existing is None else str(existing)
    new_str = None if value is None else str(value)

    if has_real_value:
        return WriteChange(
            key=key_label,
            old=old_str,
            new=new_str,
            skipped=True,
            skip_reason="user value preserved",
        )

    if value is None or value == "":
        # Nothing to set, and nothing was there — no-op.
        return WriteChange(key=key_label, old=old_str, new=new_str,
                           skipped=True, skip_reason="no default applies")

    prov[field] = value
    return WriteChange(key=key_label, old=old_str, new=new_str)

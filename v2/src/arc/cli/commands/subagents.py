"""`arc subagents` — list/show/enable/disable sub-agent specs."""
from __future__ import annotations

import sys

import arc.cli as _cli
from arc.cli.wiring import _source_label


def _cmd_subagents(
    home_override: str | None,
    *,
    action: str | None,
    spec_name: str | None,
) -> int:
    """`arc subagents` — list/show/enable/disable sub-agent specs.

    No action  → opens the setup hub on the Sub-agents section.
    list       → tabular dump of every discovered spec with source + status.
    show NAME  → pretty-print the merged spec.
    enable / disable NAME → toggle the `subagents.<name>.enabled` flag in config.yml.
    """
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.runtime.subagents.registry import SubAgentRegistry
    from arc.setup.hub import run_hub
    from arc.setup.writer import write_subagent_enablement

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    if action is None:
        result = run_hub(home, initial_section="subagents")
        if result.launch_session:
            return _cli._cmd_interactive(home_override)
        return result.rc

    cfg = load(paths.config_file)
    registry = SubAgentRegistry(arc_home=home)
    report = registry.discover(cfg.subagents.as_overrides())

    if action == "list":
        specs = registry.all_specs()
        if not specs:
            print("(no sub-agents discovered)")
            return 0
        # Column widths
        name_w = max(len("NAME"), max(len(n) for n in specs))
        prov_w = max(len("PROVIDER/MODEL"), max(len(f"{s.provider}/{s.model}") for s in specs.values()))
        src_w = max(len("SOURCE"), max(len(_source_label(s)) for s in specs.values()))
        header = f"  {'STATUS':8}  {'NAME':{name_w}}  {'PROVIDER/MODEL':{prov_w}}  {'SOURCE':{src_w}}"
        print(header)
        print("  " + "─" * (len(header) - 2))
        for name in sorted(specs):
            spec = specs[name]
            status = "ENABLED " if registry.is_enabled(name) else "DISABLED"
            pm = f"{spec.provider}/{spec.model}"
            src = _source_label(spec)
            print(f"  {status:8}  {name:{name_w}}  {pm:{prov_w}}  {src:{src_w}}")
        if report.conflicts or report.failures:
            print()
            for c in report.conflicts:
                print(f"  ⚠ name collision: {c.name!r} from {c.discovered_from} "
                      f"conflicts with {c.conflicts_with}")
            for f in report.failures:
                print(f"  ✖ load failure: {f.name!r} from {f.package}: {f.error}")
        return 0

    if action == "show":
        if not spec_name:
            print("usage: arc subagents show NAME", file=sys.stderr)
            return 2
        try:
            spec = registry.get(spec_name)
        except KeyError:
            print(f"unknown sub-agent: {spec_name!r}", file=sys.stderr)
            print(f"  available: {', '.join(sorted(registry.all_specs())) or '(none)'}",
                  file=sys.stderr)
            return 2
        print(f"sub-agent: {spec.name}")
        print(f"  source:                     {_source_label(spec)}")
        print(f"  enabled:                    {registry.is_enabled(spec.name)}")
        print(f"  description:                {spec.description}")
        print(f"  provider/model:             {spec.provider}/{spec.model}")
        print(f"  tools:                      {', '.join(spec.tools) or '(none)'}")
        print(f"  timeout_s:                  {spec.timeout_s}")
        print(f"  max_turns:                  {spec.max_turns}")
        print(f"  max_dispatches_per_session: {spec.max_dispatches_per_session}")
        print(f"  max_consecutive_failures:   {spec.max_consecutive_failures}")
        print(f"  max_transient_retries:      {spec.max_transient_retries}")
        if spec.api_key_env:
            print(f"  api_key_env:                {spec.api_key_env}")
        if spec.base_url:
            print(f"  base_url:                   {spec.base_url}")
        if spec.expected_output:
            print(f"  expected_output:            {spec.expected_output}")
        print(f"  system_prompt:              [{len(spec.system_prompt)} chars]")
        return 0

    if action in ("enable", "disable"):
        if not spec_name:
            print(f"usage: arc subagents {action} NAME", file=sys.stderr)
            return 2
        if spec_name not in registry.all_specs():
            print(f"unknown sub-agent: {spec_name!r}", file=sys.stderr)
            return 2
        changes = write_subagent_enablement(
            paths.config_file,
            name=spec_name,
            enabled=(action == "enable"),
        )
        for ch in changes:
            print(f"  {ch.key}: {ch.old} → {ch.new}")
        return 0

    print(f"unknown subagents action: {action}", file=sys.stderr)
    return 2

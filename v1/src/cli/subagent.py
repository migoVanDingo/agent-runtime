"""`arc subagent` subcommands — list, info.

Mirrors `arc plugin` in shape (see ``plugins/cli.py``). Lets the user see
which sub-agent specs are registered, their toolsets, response formats,
and any config overrides currently active.
"""
from __future__ import annotations

import argparse


def _print_list() -> None:
    """List registered sub-agent specs with their effective config."""
    from runtime.subagents import all_specs, known_specs
    from app_config import config

    names = known_specs()
    if not names:
        print("No sub-agents registered.")
        return

    print(f"Registered sub-agents ({len(names)}):")
    print()
    for spec in all_specs():
        override = config.subagents.get(spec.name)
        # Resolved (post-override) values for display
        provider = (override.provider if override and override.provider else spec.provider) or "(inherit)"
        model = (override.model if override and override.model else spec.model) or "(inherit)"
        timeout = (override.timeout_seconds if override and override.timeout_seconds is not None
                   else spec.timeout_seconds)
        max_iter = (override.max_iterations if override and override.max_iterations is not None
                    else spec.max_iterations)

        print(f"  {spec.name}")
        print(f"    description: {spec.description}")
        print(f"    provider:    {provider}{'  (overridden)' if override and override.provider else ''}")
        print(f"    model:       {model}{'  (overridden)' if override and override.model else ''}")
        print(f"    toolsets:    {', '.join(spec.toolset_names) or '(none)'}")
        print(f"    skills:      {', '.join(spec.skill_names) or '(none)'}")
        print(f"    response:    {spec.response_format}")
        print(f"    timeout:     {int(timeout)}s")
        print(f"    max iters:   {max_iter}")
        print()


def _print_info(name: str) -> None:
    """Show full detail for one sub-agent, including its system prompt."""
    from runtime.subagents import get_spec
    from app_config import config

    spec = get_spec(name)
    if spec is None:
        print(f"Sub-agent {name!r} not found.")
        print()
        print("Run `arc subagent list` to see registered names.")
        return

    override = config.subagents.get(spec.name)

    print(f"{spec.name}")
    print(f"  description: {spec.description}")
    print()
    print(f"  Effective config (after any overrides from config.yml):")
    provider = (override.provider if override and override.provider else spec.provider) or "(inherit from main)"
    model = (override.model if override and override.model else spec.model) or "(inherit from main)"
    print(f"    provider:        {provider}")
    print(f"    model:           {model}")
    print(f"    toolsets:        {', '.join(spec.toolset_names) or '(none)'}")
    print(f"    skills:          {', '.join(spec.skill_names) or '(none)'}")
    print(f"    response format: {spec.response_format}")
    timeout = (override.timeout_seconds if override and override.timeout_seconds is not None
               else spec.timeout_seconds)
    max_iter = (override.max_iterations if override and override.max_iterations is not None
                else spec.max_iterations)
    print(f"    timeout:         {int(timeout)}s")
    print(f"    max iterations:  {max_iter}")

    if spec.response_format == "json" and spec.response_schema:
        import json
        print()
        print(f"  Response schema:")
        for line in json.dumps(spec.response_schema, indent=2).splitlines():
            print(f"    {line}")

    print()
    print(f"  System prompt ({len(spec.system_prompt)} chars):")
    if not spec.system_prompt:
        print(f"    (none — inherits parent's system prompt)")
    else:
        for line in spec.system_prompt.splitlines():
            print(f"    {line}")


def cmd_subagent(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="arc subagent")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="list registered sub-agents and their effective config")
    p_info = sub.add_parser("info", help="show full detail for one sub-agent")
    p_info.add_argument("name")

    args = parser.parse_args(argv)
    # Trigger built-in spec registration by importing the modules. New
    # sub-agents added later by plugins / 0091 will register on their own.
    try:
        from tools.implementations.subagents import ghidra_analyst  # noqa: F401
    except Exception:
        pass

    if args.action == "list":
        _print_list()
    elif args.action == "info":
        _print_info(args.name)

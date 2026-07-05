"""`arc llm` — manage the local inference server.  See 0018."""
from __future__ import annotations

import sys

import arc.cli as _cli


def _cmd_llm(home_override: str | None, args) -> int:
    """No subcommand → opens the setup hub on the LLM Server section."""
    from arc import llm as _llm
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.llm.registry import RegistryError
    from arc.setup.hub import run_hub

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    action = args.llm_action
    if action is None:
        result = run_hub(home, initial_section="llm")
        if result.launch_session:
            return _cli._cmd_interactive(home_override)
        return result.rc
    try:
        if action == "list":
            return _llm.list_models(paths)
        if action == "status":
            return _llm.show_status(paths)
        if action == "start":
            return _llm.start_server(paths, args.model_id)
        if action == "stop":
            return _llm.stop_server(paths)
        if action == "restart":
            return _llm.restart_server(paths, args.model_id)
        if action == "logs":
            return _llm.show_logs(paths, tail=args.tail)
    except RegistryError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"unknown llm action: {action}", file=sys.stderr)
    return 2

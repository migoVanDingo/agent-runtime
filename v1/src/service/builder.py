"""Factory for building InProcessAgentService from scratch.

This module is the ONLY place in service/ that imports from agent.py and
runtime/. It exists so that ui/ can construct the full service without
violating the import discipline (ui/ must never import from agent.py or
runtime/).

Usage:
    from service.builder import build_service, ServiceOptions, SessionInfo
    bundle = build_service(ServiceOptions(session_id="my-session"))
    bundle.service  # InProcessAgentService
    bundle.info     # SessionInfo (session_id, session_dir, provider_line)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServiceOptions:
    session_id: str = ""
    resumed_messages: list[dict] = field(default_factory=list)
    verbose: bool = False
    project_id: str = "arc-tui"


@dataclass
class SessionInfo:
    """Display information about the current session, for the TUI banner."""
    session_id: str
    session_dir: str
    provider_line: str


@dataclass
class ServiceBundle:
    service: object  # InProcessAgentService — typed as object to avoid circular
    info: SessionInfo


def build_service(opts: ServiceOptions) -> ServiceBundle:
    """Build an InProcessAgentService and configure the logging/event stack.

    All runtime/ and agent.py imports are deferred here so ui/ stays clean.
    """
    from agent import Agent
    from app_config import config
    from logger import configure_logging
    from runtime.events import init_runtime_events
    from session_paths import session_dir as _session_dir
    from service.inprocess import InProcessAgentService

    if not opts.session_id:
        from utils import generate_id
        opts.session_id = generate_id("session")

    # Set up file logging (arc.log, council_metrics.jsonl).
    configure_logging(opts.session_id, verbose=opts.verbose)

    # Set up the structured event bus (runtime.jsonl).
    init_runtime_events(opts.session_id, project_id=opts.project_id)

    # Initialize RAG if available — guarded so missing deps don't crash.
    try:
        from rag import init_rag_service
        init_rag_service(opts.session_id)
    except Exception:
        pass

    # Initialize the artifact store so store_artifact/get_artifact/etc. work.
    # The legacy CLI path in main.py calls init_store before resolving the
    # session; the TUI service builder must do the same or the planner will
    # emit artifact tools that always fail and trigger replan loops (see
    # SES01KRV1XJ7WK4177X1KHDYEWQ4B — 6 replans wasted on store_artifact).
    try:
        if config.artifact_store.enabled:
            from runtime.artifact_store import init_store
            from session_paths import store_db_path, store_data_dir
            init_store(
                db_path=store_db_path(),
                data_dir=store_data_dir(),
                inline_threshold=config.artifact_store.inline_threshold_bytes,
            )
    except Exception:
        pass  # best-effort; monitor short-circuit catches the fallout

    # Build provider info string for the banner.
    try:
        provider = config.llm.provider
        if provider == "ollama":
            from settings import settings as _s
            provider_line = f"ollama  {_s.ollama_model}"
        else:
            rt_provider = config.llm.runtime_provider or provider
            rt_model = config.llm.runtime_model or "(default)"
            provider_line = (
                f"main: {provider} / {config.llm.model}"
                f"  ·  runtime: {rt_provider} / {rt_model}"
            )
    except Exception:
        provider_line = "provider: (unknown)"

    from service.inprocess import TUIUserGate, TUIInputGate

    # Use TUIUserGate so escalation prompts are handled by the TUI input loop
    # instead of calling input() directly (which deadlocks inside patch_stdout).
    tui_gate = TUIUserGate()

    # Inject NoopSpinner up front so the legacy `ui.spinner.Spinner` never
    # constructs at all under the TUI. Previously InProcessAgentService
    # swapped agent.spinner = NoopSpinner() after Agent.__init__ ran — the
    # swap worked for the parent, but any time a sub-agent or other Agent
    # got constructed afterwards it would build a fresh real Spinner and
    # write to stdout. Inject-at-construction prevents the real Spinner
    # from ever existing in this process.
    from service.inprocess import NoopSpinner
    agent = Agent(
        verbose=opts.verbose,
        initial_messages=opts.resumed_messages or [],
        user_gate=tui_gate,
        spinner=NoopSpinner(),
    )

    service = InProcessAgentService(agent, session_id=opts.session_id)

    # Wire TUIInputGate as the pipeline's user_input_fn so ASK_USER blocks the
    # worker thread properly and the TUI can supply the clarification response.
    input_gate = TUIInputGate()
    service.input_gate = input_gate
    agent._pipeline._user_input_fn = input_gate.ask

    info = SessionInfo(
        session_id=opts.session_id,
        session_dir=str(_session_dir(opts.session_id)),
        provider_line=provider_line,
    )

    return ServiceBundle(service=service, info=info)


def finalize_session(session_id: str) -> None:
    """Write session-end log entries. Call when the UI loop exits."""
    try:
        from runtime.token_tracker import get_tracker
        get_tracker().log_summary()
    except Exception:
        pass
    try:
        from logger import log_session_end
        log_session_end(session_id)
    except Exception:
        pass
    try:
        from runtime.persistence import PersistenceWriter
        # No-op if persistence is disabled.
    except Exception:
        pass
    try:
        from runtime.events.summary import write_session_summary
        write_session_summary(session_id, outcome="completed")
    except Exception:
        pass
    # Note: the agent process never starts a JVM anymore — every Ghidra call
    # runs in a short-lived subprocess (see tools/implementations/reversing/
    # ghidra_subprocess.py). So there's no in-process JVM to shut down here
    # and Python's normal exit handlers can run unimpeded. The Ctrl+C
    # double-tap hard-exit in ui/app_keybindings remains as defense-in-depth
    # against any future tool that might bring back the same problem.

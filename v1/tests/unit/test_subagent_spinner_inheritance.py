"""Pin the invariant from session SES01KRV1XJ7WK4177X1KHDYEWQ4B:

A sub-agent's child Agent must inherit the parent's spinner. Without
this, `Agent.__init__` constructs a fresh real `ui.spinner.Spinner` for
the child, which writes BRAILLE-dot frames directly to stdout via
carriage-return overprinting — corrupting the TUI's alt-screen render
(cursor jumps, duplicated/partial spinner lines, escalation panel shift).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_agent_constructor_accepts_spinner_kwarg():
    """Agent.__init__ has a spinner injection point — the load-bearing fix."""
    from agent import Agent
    sig = inspect.signature(Agent.__init__)
    assert "spinner" in sig.parameters
    # Default must be None so existing callers still get a real Spinner.
    assert sig.parameters["spinner"].default is None


def test_subagent_runner_passes_parent_spinner_to_child():
    """Verify the source of _build_child_agent threads parent.spinner into Agent()."""
    from runtime.subagents.runner import SubAgentRunner
    src = inspect.getsource(SubAgentRunner._build_child_agent)
    # The construction call must include spinner=parent.spinner.
    assert "spinner=parent.spinner" in src, (
        "SubAgentRunner._build_child_agent must inject parent.spinner into the "
        "child Agent constructor so the child doesn't build a fresh stdout-writing "
        "spinner under the TUI"
    )


def test_service_builder_injects_noop_spinner():
    """Verify the TUI service builder constructs the parent Agent with a NoopSpinner."""
    from service import builder
    src = inspect.getsource(builder.build_service)
    assert "spinner=NoopSpinner()" in src, (
        "service/builder.py:build_service must construct the agent with "
        "spinner=NoopSpinner() so the legacy ui.spinner.Spinner never exists "
        "under the TUI"
    )


def test_noop_spinner_implements_spinner_api():
    """NoopSpinner must match the real Spinner's API surface."""
    from service.inprocess import NoopSpinner
    from ui.spinner import Spinner

    real_methods = {
        name for name in dir(Spinner)
        if not name.startswith("_") and callable(getattr(Spinner, name))
    }
    noop_methods = {
        name for name in dir(NoopSpinner)
        if not name.startswith("_") and callable(getattr(NoopSpinner, name))
    }
    missing = real_methods - noop_methods
    assert not missing, (
        f"NoopSpinner is missing methods that Spinner has: {missing}. "
        f"Any agent.spinner.X call that hits the missing methods will raise "
        f"AttributeError under the TUI."
    )

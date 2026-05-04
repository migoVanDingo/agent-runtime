"""Prompt-injection escalation helper for fetched web content."""

from __future__ import annotations

import re
from dataclasses import dataclass

from runtime.escalation import Escalation
from tools.implementations.web.read_url import INJECTION_WARNING_PREFIX


@dataclass(frozen=True)
class InjectionGateResult:
    content: str
    approved: bool
    cancelled: bool


def handle_injection_warning(
    result: str,
    *,
    user_gate,
    spinner,
    resume_spinner_message: str,
) -> InjectionGateResult:
    """Route web-content injection warnings through the user gate.

    Existing behavior asked directly via input() inside execution stages.
    This helper keeps the same proceed/cancel semantics but centralizes the UI
    through UserGate so non-interactive gates can deny safely.
    """
    if not result.startswith(INJECTION_WARNING_PREFIX):
        return InjectionGateResult(content=result, approved=True, cancelled=False)

    display = result.replace(INJECTION_WARNING_PREFIX + "\n", "")
    spinner.stop()
    approved = user_gate.prompt(
        Escalation(
            reason=(
                "Possible prompt injection detected in fetched web content. "
                "Allow this quarantined content to enter context?"
            ),
            source="injection",
            tool_input={"warning": display[:1000]},
        )
    )
    spinner.start(resume_spinner_message)

    if approved:
        return InjectionGateResult(
            content=result.replace(
                INJECTION_WARNING_PREFIX + "\n",
                "[SECURITY REVIEW PASSED BY USER]\n",
            ),
            approved=True,
            cancelled=False,
        )

    key_match = re.search(r"Artifact-key: (\S+)", result)
    if key_match:
        try:
            from runtime.artifact_store import get_artifact_store

            get_artifact_store().expel(key_match.group(1))
        except Exception:
            pass

    return InjectionGateResult(
        content="Tool call cancelled by user: potential prompt injection in fetched content.",
        approved=False,
        cancelled=True,
    )

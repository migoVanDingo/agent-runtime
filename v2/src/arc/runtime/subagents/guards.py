"""Dispatch guards — quota, circuit breaker, retry classifier.

All state is per parent session, keyed by spec name. Owned by the
SubAgentRunner instance; one runner per parent session. Nothing persists
across sessions.

Three independent mechanisms (see _design/0020-subagent-dispatch.md):
  - Quota:   per-session per-spec cap on total dispatches
  - Circuit: hard-lock after N consecutive failures
  - Retry:   internal retry for transient errors (network, 429, 5xx)
             — does NOT consume a dispatch slot
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from arc.runtime.events import EventType


# Transient error classifier — used by the runner to decide whether to
# retry internally vs. surface to the parent.
TransientReason = Literal["network", "rate_limit", "server_error", "timeout_transient"]


def classify_error(exc: BaseException) -> TransientReason | None:
    """Return a transient reason if `exc` is retryable, else None.

    Conservative — we treat unknown errors as logical (no retry) to avoid
    masking bugs in the child's tool code or system prompt.
    """
    name = type(exc).__name__
    # httpx exception names (avoid importing httpx — providers may use
    # different clients).
    if name in ("ConnectError", "ConnectTimeout", "ReadError"):
        return "network"
    if name in ("ReadTimeout", "WriteTimeout", "PoolTimeout"):
        return "timeout_transient"
    # HTTPStatusError carries a response; check status code if available.
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        if status == 429:
            return "rate_limit"
        if 500 <= status < 600:
            return "server_error"
    # Anthropic/Gemini SDK-specific transient classes (best effort).
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError"):
        return "rate_limit" if "RateLimit" in name else "network"
    if name == "InternalServerError":
        return "server_error"
    return None


@dataclass
class _SpecCounters:
    """Per-spec state held by the DispatchGuard."""
    dispatches_used: int = 0
    consecutive_failures: int = 0
    circuit_tripped: bool = False
    quota_event_emitted: bool = False    # so we only emit quota_exceeded once per spec per session


@dataclass(frozen=True)
class GuardOutcome:
    """What the guard decided about an incoming dispatch."""
    allowed: bool
    reason: str = ""              # human-readable when denied
    fired_event_type: str | None = None  # event the runner should emit on denial


class DispatchGuard:
    """Bounds per-spec dispatch attempts per parent session.

    Lifecycle:
      runner constructs one DispatchGuard per session
      runner calls `try_acquire(spec)` before each dispatch
      runner calls `record_outcome(spec, status)` after each dispatch

    The guard never emits events itself — the runner does the emission
    with full session/turn context.
    """

    def __init__(self) -> None:
        self._counters: dict[str, _SpecCounters] = {}

    # ── Predicates ─────────────────────────────────────────────────────────

    def _get(self, spec_name: str) -> _SpecCounters:
        if spec_name not in self._counters:
            self._counters[spec_name] = _SpecCounters()
        return self._counters[spec_name]

    def dispatches_used(self, spec_name: str) -> int:
        return self._get(spec_name).dispatches_used

    def consecutive_failures(self, spec_name: str) -> int:
        return self._get(spec_name).consecutive_failures

    def is_tripped(self, spec_name: str) -> bool:
        return self._get(spec_name).circuit_tripped

    # ── Decisions ──────────────────────────────────────────────────────────

    def try_acquire(
        self,
        spec_name: str,
        *,
        max_dispatches: int,
        max_consecutive_failures: int,
    ) -> GuardOutcome:
        """Check whether `spec_name` is allowed to dispatch right now.

        Quota is checked before circuit so a parent that exhausted quota
        sees the quota error (more informative than "circuit tripped" for
        the cost-ceiling case).
        """
        c = self._get(spec_name)

        # Quota check — counter has not been incremented yet.
        if c.dispatches_used >= max_dispatches:
            # First denial emits the event; subsequent denials are silent
            # (the parent already saw quota_exceeded once).
            first = not c.quota_event_emitted
            c.quota_event_emitted = True
            return GuardOutcome(
                allowed=False,
                reason=(
                    f"sub-agent quota exceeded: {spec_name} "
                    f"{c.dispatches_used}/{max_dispatches} dispatches used this session"
                ),
                fired_event_type=EventType.SUBAGENT_QUOTA_EXCEEDED if first else None,
            )

        # Circuit check.
        if c.circuit_tripped:
            return GuardOutcome(
                allowed=False,
                reason=(
                    f"sub-agent circuit tripped: {spec_name} failed "
                    f"{c.consecutive_failures} times in a row; locked for this session"
                ),
                fired_event_type=None,  # trip event was already emitted on the trip itself
            )

        return GuardOutcome(allowed=True)

    def record_attempt(self, spec_name: str) -> None:
        """Increment the dispatch counter. Called when a dispatch begins
        (after try_acquire returns allowed=True)."""
        self._get(spec_name).dispatches_used += 1

    def record_outcome(
        self,
        spec_name: str,
        *,
        status: str,
        max_consecutive_failures: int,
    ) -> bool:
        """Update failure counter / circuit state based on result status.

        Returns True if the circuit just tripped on this outcome (caller
        emits the circuit_tripped event).
        """
        c = self._get(spec_name)
        if status in ("error", "timeout"):
            c.consecutive_failures += 1
            if (
                not c.circuit_tripped
                and c.consecutive_failures >= max_consecutive_failures
            ):
                c.circuit_tripped = True
                return True
            return False
        if status == "ok":
            c.consecutive_failures = 0
            return False
        # status == "cancelled" — user-initiated, doesn't count toward
        # spec reliability. Leave counters alone.
        return False

"""Per-spec dispatch quota enforcement."""
from __future__ import annotations

from arc.runtime.subagents.guards import DispatchGuard


def test_quota_allows_up_to_cap():
    g = DispatchGuard()
    for _ in range(3):
        outcome = g.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2)
        assert outcome.allowed
        g.record_attempt("foo")
    # 4th attempt — denied.
    outcome = g.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2)
    assert not outcome.allowed
    assert "quota exceeded" in outcome.reason


def test_quota_event_emitted_only_once():
    g = DispatchGuard()
    for _ in range(2):
        g.try_acquire("foo", max_dispatches=2, max_consecutive_failures=2)
        g.record_attempt("foo")
    # First denial — event type populated
    first = g.try_acquire("foo", max_dispatches=2, max_consecutive_failures=2)
    assert first.fired_event_type == "subagent.quota_exceeded"
    # Subsequent denials — event type None
    second = g.try_acquire("foo", max_dispatches=2, max_consecutive_failures=2)
    assert second.fired_event_type is None


def test_quota_per_spec_isolation():
    g = DispatchGuard()
    for _ in range(3):
        g.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2)
        g.record_attempt("foo")
    # bar untouched — its quota is fresh.
    outcome = g.try_acquire("bar", max_dispatches=3, max_consecutive_failures=2)
    assert outcome.allowed


def test_fresh_guard_resets_state():
    """A new DispatchGuard (= new session) starts fresh."""
    g1 = DispatchGuard()
    for _ in range(3):
        g1.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2)
        g1.record_attempt("foo")
    assert not g1.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2).allowed
    g2 = DispatchGuard()
    assert g2.try_acquire("foo", max_dispatches=3, max_consecutive_failures=2).allowed


def test_quota_counter_includes_failed_attempts():
    """The cost ceiling counts ALL attempts, not just successes."""
    g = DispatchGuard()
    for _ in range(3):
        g.try_acquire("foo", max_dispatches=3, max_consecutive_failures=10)
        g.record_attempt("foo")
        # All errored — but still count toward quota.
        g.record_outcome("foo", status="error", max_consecutive_failures=10)
    assert g.dispatches_used("foo") == 3
    assert not g.try_acquire("foo", max_dispatches=3, max_consecutive_failures=10).allowed

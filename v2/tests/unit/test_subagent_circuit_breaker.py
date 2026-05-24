"""Consecutive-failure circuit breaker."""
from __future__ import annotations

from arc.runtime.subagents.guards import DispatchGuard


def test_two_failures_trips():
    g = DispatchGuard()
    g.record_attempt("foo")
    tripped = g.record_outcome("foo", status="error", max_consecutive_failures=2)
    assert tripped is False  # first failure
    g.record_attempt("foo")
    tripped = g.record_outcome("foo", status="error", max_consecutive_failures=2)
    assert tripped is True   # second failure → trip
    assert g.is_tripped("foo")


def test_tripped_blocks_subsequent_dispatch():
    g = DispatchGuard()
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    outcome = g.try_acquire("foo", max_dispatches=10, max_consecutive_failures=2)
    assert not outcome.allowed
    assert "circuit tripped" in outcome.reason


def test_success_resets_failure_counter():
    g = DispatchGuard()
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=3)
    g.record_attempt("foo")
    g.record_outcome("foo", status="ok", max_consecutive_failures=3)
    assert g.consecutive_failures("foo") == 0
    # Now we need 3 more in a row to trip.
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=3)
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=3)
    assert not g.is_tripped("foo")
    g.record_attempt("foo")
    g.record_outcome("foo", status="error", max_consecutive_failures=3)
    assert g.is_tripped("foo")


def test_breaker_per_spec():
    g = DispatchGuard()
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    assert g.is_tripped("foo")
    assert not g.is_tripped("bar")


def test_cancelled_does_not_count():
    g = DispatchGuard()
    g.record_outcome("foo", status="cancelled", max_consecutive_failures=2)
    g.record_outcome("foo", status="cancelled", max_consecutive_failures=2)
    assert not g.is_tripped("foo")
    assert g.consecutive_failures("foo") == 0


def test_no_auto_reset_after_trip():
    """Once tripped, the only way out is a new session (= new DispatchGuard)."""
    g = DispatchGuard()
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    g.record_outcome("foo", status="error", max_consecutive_failures=2)
    assert g.is_tripped("foo")
    # Even a "success" couldn't un-trip; the dispatch wouldn't even run.
    # We don't try to call record_outcome("ok") here because record_outcome
    # would be called by the runner — and the runner can't run a tripped spec.
    assert g.is_tripped("foo")

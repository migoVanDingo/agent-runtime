"""Tests for identity contextvars + event construction."""
from __future__ import annotations

from arc.runtime.events import EventType, RuntimeEvent, SCHEMA_VERSION, Severity
from arc.runtime.ids import new_event_id, new_session_id, new_tool_call_id, new_turn_id
from arc.runtime.scope import (
    SCOPE_MAIN,
    current_parent_event_id,
    current_scope,
    current_session_id,
    current_turn_id,
    parent_event,
    scoped,
    session,
    turn,
)


# ── IDs ─────────────────────────────────────────────────────────────────────


def test_id_prefixes():
    assert new_session_id().startswith("SES")
    assert new_turn_id().startswith("TRN")
    assert new_event_id().startswith("EVT")
    assert new_tool_call_id().startswith("TCL")


def test_ids_are_unique():
    ids = {new_event_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_ids_are_sortable_across_milliseconds():
    # ULIDs are sortable by their timestamp portion. Within the same millisecond
    # they're NOT strictly ordered (need a monotonic factory for that). Test
    # the cross-millisecond claim only — that's what we actually rely on.
    import time
    a = new_event_id()
    time.sleep(0.002)
    b = new_event_id()
    assert a < b


# ── Contextvar defaults ─────────────────────────────────────────────────────


def test_defaults_outside_any_scope():
    assert current_session_id() is None
    assert current_turn_id() is None
    assert current_scope() == SCOPE_MAIN
    assert current_parent_event_id() is None


# ── Context managers ────────────────────────────────────────────────────────


def test_session_cm_sets_and_restores():
    assert current_session_id() is None
    with session("ses_abc"):
        assert current_session_id() == "ses_abc"
    assert current_session_id() is None


def test_turn_cm_nests_inside_session():
    with session("ses_x"):
        with turn("trn_y"):
            assert current_session_id() == "ses_x"
            assert current_turn_id() == "trn_y"
        assert current_turn_id() is None
        assert current_session_id() == "ses_x"


def test_scoped_cm_overrides_and_restores():
    assert current_scope() == "main"
    with scoped("subagent:ghidra"):
        assert current_scope() == "subagent:ghidra"
        with scoped("subagent:nested"):
            assert current_scope() == "subagent:nested"
        assert current_scope() == "subagent:ghidra"
    assert current_scope() == "main"


def test_parent_event_cm_chains():
    assert current_parent_event_id() is None
    with parent_event("evt_outer"):
        assert current_parent_event_id() == "evt_outer"
        with parent_event("evt_inner"):
            assert current_parent_event_id() == "evt_inner"
        assert current_parent_event_id() == "evt_outer"


# ── Event construction ─────────────────────────────────────────────────────


def test_event_autofills_identity_from_contextvars():
    with session("ses_test"), turn("trn_test"), scoped("main"), parent_event("evt_parent"):
        e = RuntimeEvent(type=EventType.LLM_CALL_STARTED)
        assert e.session_id == "ses_test"
        assert e.turn_id == "trn_test"
        assert e.scope == "main"
        assert e.parent_event_id == "evt_parent"


def test_event_gets_unique_event_id():
    e1 = RuntimeEvent(type=EventType.TURN_STARTED)
    e2 = RuntimeEvent(type=EventType.TURN_STARTED)
    assert e1.event_id != e2.event_id
    assert e1.event_id.startswith("EVT")


def test_event_to_dict_field_order_matches_spec():
    """Per §6.1, envelope order is well-defined. to_dict() must preserve it."""
    with session("ses_x"), turn("trn_y"):
        e = RuntimeEvent(
            type=EventType.LLM_CALL_COMPLETED,
            payload={"a": 1},
            content={"b": 2},
            duration_ms=42,
        )
    d = e.to_dict()
    keys = list(d.keys())
    expected_prefix = [
        "event_id", "session_id", "turn_id", "scope", "parent_event_id",
        "ts", "ts_monotonic_ns", "type", "stage", "severity", "duration_ms",
        "payload", "content", "schema_version",
    ]
    assert keys == expected_prefix


def test_event_timestamps_are_increasing():
    e1 = RuntimeEvent(type=EventType.EVENT_EMITTED)
    e2 = RuntimeEvent(type=EventType.EVENT_EMITTED)
    assert e2.ts_monotonic_ns > e1.ts_monotonic_ns


def test_event_default_severity_is_info():
    e = RuntimeEvent(type=EventType.TURN_STARTED)
    assert e.severity == Severity.INFO


def test_event_schema_version_is_set():
    e = RuntimeEvent(type=EventType.TURN_STARTED)
    assert e.schema_version == SCHEMA_VERSION == 1


def test_payload_content_preserve_arbitrary_data():
    """Critical for byte-fidelity: dicts go in untouched."""
    weird = {
        "nested": {"deeply": {"nested": [1, 2, {"value": "preserved"}]}},
        "with_unicode": "héllo wörld 🌍",
        "with_floats": 3.14159,
        "with_none": None,
        "empty_list": [],
        "empty_dict": {},
    }
    e = RuntimeEvent(type=EventType.LLM_CALL_COMPLETED, content=weird)
    d = e.to_dict()
    assert d["content"] == weird

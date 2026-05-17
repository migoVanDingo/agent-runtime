"""Tests for Phase C: event coverage from pipeline and provider."""
import pytest
from runtime.events import EventBus, RuntimeEvent
from runtime.identity import RuntimeIdentity
from runtime.pipeline import Pipeline
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus


class _CollectSink:
    def __init__(self): self.events = []
    def emit(self, event): self.events.append(event)

    def types(self):
        return [e.event_type for e in self.events]


class _OKStage(Stage):
    name = "OKStage"
    def run(self, ctx): return StageResult(StageStatus.OK, ctx)

class _DoneStage(Stage):
    name = "DoneStage"
    def run(self, ctx):
        ctx.response = "done"
        return StageResult(StageStatus.DONE, ctx)

class _AbortStage(Stage):
    name = "AbortStage"
    def run(self, ctx): return StageResult(StageStatus.ABORT, ctx, reason="test abort")


def _run_pipeline(*stages, with_sink=None):
    """Run a pipeline with a collect sink wired in; returns (response, sink)."""
    import runtime.events.runtime as _rt
    sink = with_sink or _CollectSink()
    old_bus = _rt._event_bus
    _rt._event_bus = EventBus([sink])
    _rt._identity = RuntimeIdentity.new_session(session_id="TEST")

    fallback = _DoneStage()
    pipe = Pipeline(stages=list(stages), fallback_stage=fallback, user_input_fn=lambda q: "yes")
    ctx = PipelineContext(
        user_message="test",
        identity=RuntimeIdentity.new_session(session_id="TEST"),
    )
    resp = pipe.run(ctx)
    _rt._event_bus = old_bus
    return resp, sink


def test_pipeline_emits_stage_started_and_finished():
    _, sink = _run_pipeline(_OKStage(), _DoneStage())
    types = sink.types()
    assert "stage.started" in types
    assert "stage.finished" in types


def test_stage_started_finished_pairs_for_each_stage():
    _, sink = _run_pipeline(_OKStage(), _OKStage(), _DoneStage())
    started = [e for e in sink.events if e.event_type == "stage.started"]
    finished = [e for e in sink.events if e.event_type == "stage.finished"]
    assert len(started) == len(finished)
    assert len(started) >= 2  # at least 2 OK stages ran


def test_stage_finished_includes_status():
    _, sink = _run_pipeline(_OKStage(), _DoneStage())
    finished = [e for e in sink.events if e.event_type == "stage.finished"]
    statuses = {e.payload["status"] for e in finished}
    # Should have ok and done
    assert "ok" in statuses or "done" in statuses


def test_abort_emits_stage_finished_with_abort():
    _, sink = _run_pipeline(_AbortStage(), _DoneStage())
    finished = [e for e in sink.events if e.event_type == "stage.finished"]
    statuses = {e.payload["status"] for e in finished}
    assert "abort" in statuses


def test_stage_events_carry_pipeline_run_id():
    _, sink = _run_pipeline(_OKStage(), _DoneStage())
    stage_events = [e for e in sink.events
                    if e.event_type in ("stage.started", "stage.finished")]
    assert all(e.identity.pipeline_run_id is not None for e in stage_events)


def test_stage_events_share_pipeline_run_id():
    _, sink = _run_pipeline(_OKStage(), _DoneStage())
    stage_events = [e for e in sink.events
                    if e.event_type in ("stage.started", "stage.finished")]
    run_ids = {e.identity.pipeline_run_id for e in stage_events}
    assert len(run_ids) == 1

"""Tests for Phase B: identity propagation through PipelineContext."""
import pytest
from runtime.identity import RuntimeIdentity
from runtime.pipeline import Pipeline
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus


class _CollectSink:
    def __init__(self):
        self.events = []
    def emit(self, event):
        self.events.append(event)


class _RecordIdentityStage(Stage):
    name = "RecordIdentity"
    def __init__(self):
        self.captured = None
    def run(self, context):
        self.captured = context.identity
        return StageResult(status=StageStatus.OK, updated_context=context)


class _DoneStage(Stage):
    name = "DoneStage"
    def run(self, context):
        context.response = "done"
        return StageResult(status=StageStatus.DONE, updated_context=context)


def _make_pipeline(*stages):
    fallback = _DoneStage()
    return Pipeline(stages=list(stages), fallback_stage=fallback, user_input_fn=lambda q: "yes")


def test_pipeline_mints_pipeline_run_id():
    recorder = _RecordIdentityStage()
    pipe = _make_pipeline(recorder, _DoneStage())
    ctx = PipelineContext(
        user_message="hello",
        identity=RuntimeIdentity.new_session(session_id="SESS1"),
    )
    pipe.run(ctx)
    assert recorder.captured is not None
    assert recorder.captured.pipeline_run_id is not None
    assert recorder.captured.session_id == "SESS1"


def test_pipeline_preserves_session_id_across_stages():
    r1, r2 = _RecordIdentityStage(), _RecordIdentityStage()
    pipe = _make_pipeline(r1, r2, _DoneStage())
    ctx = PipelineContext(
        user_message="hello",
        identity=RuntimeIdentity.new_session(session_id="SESS2"),
    )
    pipe.run(ctx)
    assert r1.captured.session_id == "SESS2"
    assert r2.captured.session_id == "SESS2"
    assert r1.captured.pipeline_run_id == r2.captured.pipeline_run_id


def test_context_identity_defaults_to_none_without_pipeline():
    ctx = PipelineContext(user_message="hello")
    assert ctx.identity is None

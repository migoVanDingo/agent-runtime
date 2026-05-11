from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planning.schema import Plan
    from runtime.schema import ClassifierResult, ContinuationState
    from runtime.identity import RuntimeIdentity


@dataclass
class PipelineContext:
    """Shared state that flows through every pipeline stage.

    Stages read from this and write back to it.  The pipeline runner
    passes the updated context from each StageResult into the next stage.

    Fields are grouped by the stage that first populates them.
    """

    # ── Set at pipeline entry (call-site) ────────────────────────────
    user_message: str
    # RAG historical context block — injected into system prompt by execution stages.
    # Built per-turn in agent.call() before the pipeline runs.
    rag_context: str = ""
    # Runtime identity — minted by Pipeline.run, enriched by stages.
    identity: "RuntimeIdentity | None" = None

    # ── Set by RoutingStage ──────────────────────────────────────────
    # Packed (compressed) conversation messages from ContextManager.pack().
    packed_messages: list[dict] = field(default_factory=list)
    # Parsed classification from the routing response.
    classification: ClassifierResult | None = None
    # Text that came after the <route> block in the routing response.
    # Non-empty only when mode="direct" and the model answered inline.
    answer_text: str = ""
    # Text-only view of packed_messages (tool results excluded).
    # Used by EntityCriticStage as the candidate source for path correction.
    entity_context: str | None = None

    # ── Set by SkillHintStage (advisory only) ────────────────────────
    # Name of a skill the planner is hinted to use. NOT load-bearing.
    skill_hint: str | None = None

    # ── Set by SkillExpansionStage ────────────────────────────────────
    # Single-skill plans stamp this for ContinuationStage criteria lookup.
    # Multi-skill plans and planner-only plans leave it None.
    active_skill_name: str | None = None

    # ── Legacy informational fields (not load-bearing) ────────────────
    routing_path: str | None = None
    workflow_name: str | None = None

    # ── Set by PlanningStage ─────────────────────────────────────────
    plan: Plan | None = None

    # ── Continuation state (managed by ContinuationStage) ────────────
    continuation_state: "ContinuationState | None" = None

    # ── Set by ExecutionStage or SynthesizerStage ────────────────────
    response: str | None = None

    # ── Pipeline runner control ──────────────────────────────────────
    # Number of times the current stage has been retried.
    # Reset to 0 by the runner each time it advances to a new stage.
    retry_count: int = 0
    # Failure reason injected by the runner when status=RETRY.
    # Stages read this on their second run to adjust behavior.
    failure_reason: str | None = None

    # ── Persistence (optional — gated by ENABLE_SESSION_PERSISTENCE) ────
    # Set by Agent.chat() before pipeline runs, read by ExecutionStage.
    db_session_id: str | None = None

    # ── Streaming (optional) ─────────────────────────────────────────
    # When set, SynthesizerStage calls this with each token chunk instead
    # of buffering the full response. Caller receives tokens in real time.
    on_token: object = None  # Callable[[str], None] | None

    # ── Pause/cancel check (optional) ────────────────────────────────
    # Set by InProcessAgentService (via agent.call checkpoint_fn) before each turn.
    # Called between pipeline stages and between tool-loop iterations.
    # May raise TurnCancelledError to abort the turn.
    # None when running under the legacy CLI (no service layer).
    _pause_check: object = None  # Callable[[], None] | None

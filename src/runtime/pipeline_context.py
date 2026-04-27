from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planning.schema import Plan
    from runtime.schema import ClassifierResult


@dataclass
class PipelineContext:
    """Shared state that flows through every pipeline stage.

    Stages read from this and write back to it.  The pipeline runner
    passes the updated context from each StageResult into the next stage.

    Fields are grouped by the stage that first populates them.
    """

    # ── Set at pipeline entry (call-site) ────────────────────────────
    user_message: str

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

    # ── Set by WorkflowMatchStage ────────────────────────────────────
    # How the plan was produced: classifier_hint | classifier_hint_direct |
    # regex | fallback | planner | None (not yet determined)
    routing_path: str | None = None
    # Workflow name chosen during WorkflowMatchStage, if any.
    workflow_name: str | None = None

    # ── Set by WorkflowMatchStage or PlanningStage ───────────────────
    plan: Plan | None = None

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

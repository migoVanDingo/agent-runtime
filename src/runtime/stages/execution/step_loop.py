"""Step loop state and monitor-decision dispatcher for ExecutionStage.

_StepLoopState holds all mutable iteration state for _execute_plan.
apply_decision() dispatches one monitor verdict and mutates state accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from planning.schema import Plan, Step, StepStatus
from runtime.schema import StepAssessment as MonitorAssessment
from runtime.schema import StepDecision
from logger import get_logger

logger = get_logger(__name__)


@dataclass
class _StepLoopState:
    """Mutable iteration state for the step execution loop.

    Passed into apply_decision() so decision handlers can mutate idx, queue,
    plan, and replan_count without needing direct access to _execute_plan locals.
    """
    queue: list[Step]
    idx: int
    replan_count: int
    plan_start_index: int
    db_plan_id: int | None = None

    @classmethod
    def from_plan(cls, plan: Plan, messenger) -> "_StepLoopState":
        return cls(
            queue=list(plan.steps),
            idx=0,
            replan_count=0,
            plan_start_index=len(messenger.get_messages()),
        )

    def has_more(self) -> bool:
        return self.idx < len(self.queue)

    def current(self) -> Step:
        return self.queue[self.idx]

    def step_display(self) -> int:
        return self.idx + 1


def _handle_continue(
    state: _StepLoopState,
    step: Step,
) -> None:
    """Mark step completed and advance index.

    NOTE: step.completed event with timing is emitted by _execute_plan (it holds _step_t0).
    """
    step.status = StepStatus.COMPLETED
    n_total = len(state.queue)
    logger.info(f"  step {state.step_display()}/{n_total} complete")
    state.idx += 1


def _handle_retry(state: _StepLoopState, step: Step, max_retries: int) -> None:
    if step.flags.retry_count >= max_retries:
        logger.info(f"  max retries ({max_retries}) reached — continuing anyway")
        step.status = StepStatus.COMPLETED
        state.idx += 1
    else:
        step.flags.retry_count += 1
        step.status = StepStatus.PENDING
        logger.info(f"  retrying step ({step.flags.retry_count}/{max_retries})")


def _handle_replan(
    state: _StepLoopState,
    plan: Plan,
    step: Step,
    assessment: MonitorAssessment,
    planner,
    skill_expansion,
    db_session_id: str | None,
) -> None:
    from runtime.persistence import PersistenceWriter
    from runtime.events import RuntimeEvent, get_event_bus
    logger.info("  replanning")
    identity = None  # NOTE: identity not threaded into here; caller emits replan event
    new_steps = planner.replan(plan, step, assessment.reason)
    if new_steps:
        # Runtime owns skill expansion — expand any skill: references in replanned steps
        # the same way SkillExpansionStage does at pipeline start.
        if skill_expansion is not None:
            new_steps = skill_expansion.expand_steps(new_steps, plan.original_query)
        state.replan_count += 1
        state.queue = state.queue[:state.idx] + new_steps
        plan.steps = list(state.queue)
        logger.info(f"  replanned: {len(new_steps)} new step(s)")
        for s in new_steps:
            logger.info(f"    Step {s.step} [{s.action_type.value}]: {s.description}")
        state.db_plan_id = PersistenceWriter.record_plan(
            db_session_id or "",
            plan_index=state.replan_count,
            original_query=plan.original_query,
            steps=[
                {"step": s.step, "action_type": s.action_type.value, "description": s.description}
                for s in new_steps
            ],
            replan_reason=assessment.reason,
        )
    else:
        logger.info("  replan failed — marking step complete and continuing")
        step.status = StepStatus.COMPLETED
        state.idx += 1


def _handle_defer(state: _StepLoopState, plan: Plan, step: Step) -> None:
    if step.flags.deferred or step.flags.retry_count > 0:
        logger.info("  already deferred once — continuing anyway")
        step.status = StepStatus.COMPLETED
        state.idx += 1
    else:
        step.flags.deferred = True
        step.status = StepStatus.PENDING
        state.queue.pop(state.idx)
        state.queue.append(step)
        plan.steps = list(state.queue)
        logger.info(f"  deferred to end of queue (now position {len(state.queue)})")


def _handle_skip(state: _StepLoopState, step: Step, reason: str) -> None:
    step.flags.skipped = True
    step.status = StepStatus.COMPLETED
    logger.info(f"  skipped: {reason}")
    state.idx += 1


def _handle_goal_achieved(
    state: _StepLoopState,
    step: Step,
    identity,
) -> None:
    from runtime.events import RuntimeEvent, get_event_bus
    n_total = len(state.queue)
    step.status = StepStatus.COMPLETED
    logger.info(f"  goal achieved at step {state.step_display()}/{n_total}")
    if identity is not None:
        get_event_bus().emit(RuntimeEvent(
            "goal.achieved",
            identity,
            payload={"at_step": step.step, "remaining": n_total - step.step},
            stage="ExecutionStage",
        ))
    for remaining in state.queue[state.idx + 1:]:
        remaining.status = StepStatus.COMPLETED
        remaining.flags.skipped = True
        remaining.result = "(skipped — goal achieved earlier)"
    state.idx = len(state.queue)


def _handle_escalate(
    state: _StepLoopState,
    step: Step,
    assessment: MonitorAssessment,
    user_gate,
) -> None:
    from runtime.escalation import Escalation
    logger.info(f"  ESCALATE requested by monitor: {assessment.reason}")
    escalation = Escalation(
        reason=f"Monitor flagged step {step.step}: {assessment.reason}",
        source="monitor",
    )
    if user_gate.prompt(escalation):
        logger.info("  user approved — continuing")
        step.status = StepStatus.COMPLETED
    else:
        logger.info("  user denied — skipping step")
        step.flags.skipped = True
        step.status = StepStatus.COMPLETED
        step.error = "user denied escalation"
    state.idx += 1


def apply_decision(
    state: _StepLoopState,
    plan: Plan,
    step: Step,
    assessment: MonitorAssessment,
    *,
    planner,
    skill_expansion,
    db_session_id: str | None,
    identity,
    user_gate,
    max_retries: int,
) -> None:
    """Mutate state/plan/step in place according to assessment.decision.

    Handles CONTINUE / RETRY / REPLAN / DEFER / SKIP / GOAL_ACHIEVED / ESCALATE.
    Emits replan.triggered / goal.achieved events.
    NOTE: step.completed (with timing) is emitted by _execute_plan after returning.
    """
    from runtime.events import RuntimeEvent, get_event_bus
    decision = assessment.decision

    if decision == StepDecision.CONTINUE:
        _handle_continue(state, step)

    elif decision == StepDecision.RETRY:
        _handle_retry(state, step, max_retries)

    elif decision == StepDecision.REPLAN:
        if identity is not None:
            get_event_bus().emit(RuntimeEvent(
                "replan.triggered",
                identity,
                payload={"failed_step": step.step, "reason": assessment.reason},
                stage="ExecutionStage",
            ))
        _handle_replan(state, plan, step, assessment, planner, skill_expansion, db_session_id)
        # Emit the full new plan after a successful replan so analysts can
        # diff plan versions across the same turn.
        if identity is not None:
            get_event_bus().emit(RuntimeEvent(
                "plan.replanned",
                identity,
                payload={
                    "replan_count": state.replan_count,
                    "n_steps": len(plan.steps),
                    "reason": assessment.reason,
                },
                content={"plan": plan.to_dict()},
                stage="ExecutionStage",
            ))

    elif decision == StepDecision.DEFER:
        _handle_defer(state, plan, step)

    elif decision == StepDecision.SKIP:
        _handle_skip(state, step, assessment.reason)

    elif decision == StepDecision.GOAL_ACHIEVED:
        _handle_goal_achieved(state, step, identity)

    elif decision == StepDecision.ESCALATE:
        _handle_escalate(state, step, assessment, user_gate)

"""PersistenceWriter — feature-flagged writes to the agent-runtime database.

All methods are no-ops when settings.enable_session_persistence is False.
This module is the single integration point between the runtime and the DAL.

Usage:
    # At session start (Agent.chat):
    db_session_id = PersistenceWriter.start_session(query, model, provider)

    # After each step (ExecutionStage._execute_plan):
    PersistenceWriter.record_step(db_session_id, plan_id, step, result)

    # At session end (Agent.chat):
    PersistenceWriter.finish_session(db_session_id, total_steps, error)

    # When a plan is created (ExecutionStage):
    plan_id = PersistenceWriter.record_plan(db_session_id, plan_index, query, steps)
"""
from __future__ import annotations

from typing import Optional

from app_config import settings
from db.sync import run_async
from logger import get_logger

logger = get_logger(__name__)


class PersistenceWriter:
    """Synchronous façade over async DAL — all methods are safe to call from sync code."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_session_persistence

    @staticmethod
    def start_session(
        original_query: str,
        model: str,
        provider: str,
    ) -> Optional[str]:
        """Create an AgentSession row and return its ID, or None if persistence is off."""
        if not PersistenceWriter.enabled():
            return None
        try:
            return run_async(_create_session(original_query, model, provider))
        except Exception as e:
            logger.warning(f"persistence: start_session failed: {e}")
            return None

    @staticmethod
    def record_plan(
        db_session_id: str,
        plan_index: int,
        original_query: str,
        steps: list,
        replan_reason: Optional[str] = None,
    ) -> Optional[str]:
        """Create a Plan row and return its ID, or None on failure."""
        if not PersistenceWriter.enabled() or not db_session_id:
            return None
        try:
            return run_async(
                _create_plan(db_session_id, plan_index, original_query, steps, replan_reason)
            )
        except Exception as e:
            logger.warning(f"persistence: record_plan failed: {e}")
            return None

    @staticmethod
    def record_step(
        db_session_id: str,
        db_plan_id: str,
        step_index: int,
        action_type: str,
        description: str,
        tool: Optional[str],
        status: str,
        result: Optional[str],
        error: Optional[str],
        retry_count: int,
        importance_score: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Create or update a Step row. Silently ignores failures."""
        if not PersistenceWriter.enabled() or not db_session_id or not db_plan_id:
            return
        try:
            run_async(_upsert_step(
                db_session_id=db_session_id,
                db_plan_id=db_plan_id,
                step_index=step_index,
                action_type=action_type,
                description=description,
                tool=tool,
                status=status,
                result=result,
                error=error,
                retry_count=retry_count,
                importance_score=importance_score,
                duration_ms=duration_ms,
            ))
        except Exception as e:
            logger.warning(f"persistence: record_step failed: {e}")

    @staticmethod
    def record_artifact(
        db_session_id: str,
        key: str,
        tier: str,
        size_bytes: Optional[int] = None,
        content_preview: Optional[str] = None,
        storage_path: Optional[str] = None,
    ) -> None:
        """Create an Artifact row. Silently ignores failures."""
        if not PersistenceWriter.enabled() or not db_session_id:
            return
        try:
            run_async(_create_artifact(
                session_id=db_session_id,
                key=key,
                tier=tier,
                size_bytes=size_bytes,
                content_preview=content_preview,
                storage_path=storage_path,
            ))
        except Exception as e:
            logger.warning(f"persistence: record_artifact failed: {e}")

    @staticmethod
    def finish_session(
        db_session_id: str,
        *,
        total_steps: int,
        error: Optional[str] = None,
    ) -> None:
        """Mark the session completed or errored. Silently ignores failures."""
        if not PersistenceWriter.enabled() or not db_session_id:
            return
        try:
            run_async(_finish_session(db_session_id, total_steps=total_steps, error=error))
        except Exception as e:
            logger.warning(f"persistence: finish_session failed: {e}")


# ── Async helpers ──────────────────────────────────────────────────────────────

async def _create_session(query: str, model: str, provider: str) -> str:
    from db.session import agent_session
    from db.dal.agent_session_dal import AgentSessionDAL
    async with agent_session() as s:
        dal = AgentSessionDAL(s)
        obj = await dal.create(original_query=query, model=model, provider=provider)
        return obj.id


async def _create_plan(
    session_id: str,
    plan_index: int,
    original_query: str,
    steps: list,
    replan_reason: Optional[str],
) -> str:
    from db.session import agent_session
    from db.dal.plan_dal import PlanDAL
    async with agent_session() as s:
        dal = PlanDAL(s)
        obj = await dal.create(
            session_id=session_id,
            plan_index=plan_index,
            original_query=original_query,
            steps=steps,
            replan_reason=replan_reason,
        )
        return obj.id


async def _upsert_step(
    *,
    db_session_id: str,
    db_plan_id: str,
    step_index: int,
    action_type: str,
    description: str,
    tool: Optional[str],
    status: str,
    result: Optional[str],
    error: Optional[str],
    retry_count: int,
    importance_score: Optional[str],
    duration_ms: Optional[int],
) -> None:
    from db.session import agent_session
    from db.dal.plan_dal import StepDAL
    from sqlmodel import select
    from db.models.plan import Step
    async with agent_session() as s:
        dal = StepDAL(s)
        # Check if a step at this index already exists (retry scenario)
        existing_stmt = select(Step).where(
            Step.plan_id == db_plan_id,
            Step.step_index == step_index,
        )
        res = await s.execute(existing_stmt)
        existing = res.scalar_one_or_none()
        if existing:
            await dal.update_result(
                existing.id,
                status=status,
                result=result,
                error=error,
                retry_count=retry_count,
                importance_score=importance_score,
                duration_ms=duration_ms,
            )
        else:
            step_obj = await dal.create(
                plan_id=db_plan_id,
                session_id=db_session_id,
                step_index=step_index,
                action_type=action_type,
                description=description,
                tool=tool,
                status=status,
            )
            await dal.update_result(
                step_obj.id,
                status=status,
                result=result,
                error=error,
                retry_count=retry_count,
                importance_score=importance_score,
                duration_ms=duration_ms,
            )


async def _create_artifact(
    *,
    session_id: str,
    key: str,
    tier: str,
    size_bytes: Optional[int],
    content_preview: Optional[str],
    storage_path: Optional[str],
) -> None:
    from db.session import agent_session
    from db.dal.artifact_dal import ArtifactDAL
    async with agent_session() as s:
        dal = ArtifactDAL(s)
        await dal.create(
            session_id=session_id,
            key=key,
            tier=tier,
            size_bytes=size_bytes,
            content_preview=content_preview,
            storage_path=storage_path,
        )


async def _finish_session(
    session_id: str,
    *,
    total_steps: int,
    error: Optional[str],
) -> None:
    from db.session import agent_session
    from db.dal.agent_session_dal import AgentSessionDAL
    async with agent_session() as s:
        dal = AgentSessionDAL(s)
        if error:
            await dal.mark_error(session_id, error=error, total_steps=total_steps)
        else:
            await dal.mark_completed(session_id, total_steps=total_steps)

"""PlanDAL / StepDAL — CRUD for plan and step tables."""
from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.base import utcnow
from db.dal.base_dal import BaseDAL
from db.models.plan import Plan, Step


class PlanDAL(BaseDAL[Plan]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Plan, session)

    async def create(
        self,
        *,
        session_id: str,
        plan_index: int,
        original_query: str,
        steps: list,
        replan_reason: Optional[str] = None,
    ) -> Plan:
        obj = Plan(
            session_id=session_id,
            plan_index=plan_index,
            original_query=original_query,
            steps_json=json.dumps(steps),
            replan_reason=replan_reason,
        )
        return await self.save(obj)

    async def list_by_session(self, session_id: str) -> List[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.session_id == session_id, Plan.is_active == True)
            .order_by(Plan.plan_index.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class StepDAL(BaseDAL[Step]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Step, session)

    async def create(
        self,
        *,
        plan_id: str,
        session_id: str,
        step_index: int,
        action_type: str,
        description: str,
        tool: Optional[str] = None,
        status: str = "pending",
    ) -> Step:
        obj = Step(
            plan_id=plan_id,
            session_id=session_id,
            step_index=step_index,
            action_type=action_type,
            description=description,
            tool=tool,
            status=status,
        )
        return await self.save(obj)

    async def update_result(
        self,
        step_id: str,
        *,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
        retry_count: int = 0,
        importance_score: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[Step]:
        obj = await self.get_by_id(step_id)
        if obj is None:
            return None
        obj.status = status
        obj.result = result[:1000] if result else None
        obj.error = error[:500] if error else None
        obj.retry_count = retry_count
        obj.importance_score = importance_score
        obj.duration_ms = duration_ms
        return await self.save(obj)

    async def list_by_plan(self, plan_id: str) -> List[Step]:
        stmt = (
            select(Step)
            .where(Step.plan_id == plan_id, Step.is_active == True)
            .order_by(Step.step_index.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_session(self, session_id: str) -> List[Step]:
        stmt = (
            select(Step)
            .where(Step.session_id == session_id, Step.is_active == True)
            .order_by(Step.step_index.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

"""Database persistence helpers for MingLi evaluation runs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import EvaluationItem, EvaluationRun
from .dataset import EvaluationQuestion, MingLiDataset

ACTIVE_RUN_STATUSES = ("queued", "running", "cancel_requested")


class EvaluationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_run(self) -> EvaluationRun | None:
        result = await self.session.execute(
            select(EvaluationRun)
            .where(EvaluationRun.status.in_(ACTIVE_RUN_STATUSES))
            .order_by(EvaluationRun.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        run: EvaluationRun,
        questions: tuple[EvaluationQuestion, ...],
        dataset: MingLiDataset,
    ) -> EvaluationRun:
        self.session.add(run)
        await self.session.flush()
        self.session.add_all(
            [
                EvaluationItem(
                    run_id=run.id,
                    question_id=question.id,
                    case_id=question.case_id,
                    benchmark_year=question.benchmark_year,
                    category=question.category,
                    correct_answer=dataset.answer_for(question.id),
                )
                for question in questions
            ]
        )
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: UUID) -> EvaluationRun | None:
        return await self.session.get(EvaluationRun, run_id)

    async def list_runs(self, *, limit: int) -> tuple[list[EvaluationRun], int]:
        total = await self.session.scalar(select(func.count()).select_from(EvaluationRun))
        result = await self.session.execute(
            select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
        )
        return list(result.scalars()), int(total or 0)

    async def all_items(self, run_id: UUID) -> list[EvaluationItem]:
        result = await self.session.execute(
            select(EvaluationItem)
            .where(EvaluationItem.run_id == run_id)
            .order_by(EvaluationItem.id)
        )
        return list(result.scalars())

    async def get_item(self, run_id: UUID, item_id: int) -> EvaluationItem | None:
        result = await self.session.execute(
            select(EvaluationItem).where(
                EvaluationItem.run_id == run_id,
                EvaluationItem.id == item_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_items(
        self,
        run_id: UUID,
        *,
        offset: int,
        limit: int,
        result_filter: str | None,
    ) -> tuple[list[EvaluationItem], int]:
        conditions = [EvaluationItem.run_id == run_id]
        if result_filter == "correct":
            conditions.extend(
                [EvaluationItem.status == "completed", EvaluationItem.is_correct.is_(True)]
            )
        elif result_filter == "incorrect":
            conditions.extend(
                [EvaluationItem.status == "completed", EvaluationItem.is_correct.is_(False)]
            )
        elif result_filter == "error":
            conditions.append(EvaluationItem.status == "error")
        total = await self.session.scalar(
            select(func.count()).select_from(EvaluationItem).where(*conditions)
        )
        result = await self.session.execute(
            select(EvaluationItem)
            .where(*conditions)
            .order_by(EvaluationItem.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), int(total or 0)

    async def request_cancel(self, run: EvaluationRun) -> EvaluationRun:
        from ...auth import utc_now

        if run.status not in ACTIVE_RUN_STATUSES:
            return run
        run.status = "cancel_requested"
        run.cancel_requested_at = utc_now()
        await self.session.commit()
        await self.session.refresh(run)
        return run


def get_evaluation_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EvaluationRepository:
    return EvaluationRepository(session)


EvaluationRepositoryDependency = Annotated[
    EvaluationRepository,
    Depends(get_evaluation_repository),
]

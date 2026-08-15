"""PostgreSQL persistence for automatic graph organizing jobs."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import GraphOrganizingJob, GraphOrganizingTrace

ACTIVE_GRAPH_JOB_STATUSES = ("queued", "analyzing")


class GraphOrganizerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, job: GraphOrganizingJob) -> GraphOrganizingJob:
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get(self, job_id: UUID) -> GraphOrganizingJob | None:
        return await self.session.get(GraphOrganizingJob, job_id)

    async def active_for_document(self, document_id: UUID) -> GraphOrganizingJob | None:
        result = await self.session.execute(
            select(GraphOrganizingJob)
            .where(
                GraphOrganizingJob.document_id == document_id,
                GraphOrganizingJob.status.in_(ACTIVE_GRAPH_JOB_STATUSES),
            )
            .order_by(GraphOrganizingJob.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(self) -> list[GraphOrganizingJob]:
        result = await self.session.execute(
            select(GraphOrganizingJob).order_by(GraphOrganizingJob.created_at.desc()).limit(20)
        )
        return list(result.scalars())

    async def list_traces(self, job_id: UUID) -> list[GraphOrganizingTrace]:
        result = await self.session.execute(
            select(GraphOrganizingTrace)
            .where(GraphOrganizingTrace.job_id == job_id)
            .order_by(
                GraphOrganizingTrace.section_index,
                GraphOrganizingTrace.attempt,
            )
        )
        return list(result.scalars())

    async def get_trace(
        self,
        job_id: UUID,
        trace_id: int,
    ) -> GraphOrganizingTrace | None:
        result = await self.session.execute(
            select(GraphOrganizingTrace).where(
                GraphOrganizingTrace.id == trace_id,
                GraphOrganizingTrace.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()


def get_graph_organizer_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GraphOrganizerRepository:
    return GraphOrganizerRepository(session)


GraphOrganizerRepositoryDependency = Annotated[
    GraphOrganizerRepository,
    Depends(get_graph_organizer_repository),
]

"""Single durable background queue for administrator-triggered evaluations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import func, select

from ...auth import utc_now
from ...bazi.tool import BaziChartToolResult
from ...credentials import LOCAL_CREDENTIAL_SCOPE, ModelCredentialRepository
from ...database import SessionFactory
from ...models import EvaluationItem, EvaluationRun
from ...security import SecretCipher
from .context import build_evaluation_prompt
from .dataset import EvaluationQuestion, load_dataset
from .model_client import (
    EvaluationModelError,
    model_timeout,
    request_evaluation_answer,
)
from .repository import ACTIVE_RUN_STATUSES


@dataclass(frozen=True)
class ItemOutcome:
    item_id: int
    predicted_answer: str | None
    is_correct: bool
    status: str
    confidence: int | None
    reasoning_summary: str | None
    error_message: str | None
    latency_ms: int | None
    input_tokens: int
    output_tokens: int
    prompt_sha256: str | None
    agent_trace: dict | None
    fatal: bool = False


async def _credential_and_run(run_id: UUID) -> tuple[EvaluationRun, str]:
    async with SessionFactory() as session:
        run = await session.get(EvaluationRun, run_id)
        if run is None:
            raise RuntimeError("评测运行不存在")
        credential = await ModelCredentialRepository(session).get(LOCAL_CREDENTIAL_SCOPE)
        if credential is None:
            raise RuntimeError("模型 API 尚未由管理员配置")
        try:
            api_key = SecretCipher.from_environment().decrypt(
                credential.encrypted_api_key,
                scope=credential.scope,
                key_version=credential.encryption_key_version,
            )
        except Exception as exc:
            raise RuntimeError("模型 API 密钥无法解密") from exc
        return run, api_key


async def _run_status(run_id: UUID) -> str | None:
    async with SessionFactory() as session:
        run = await session.get(EvaluationRun, run_id)
        return run.status if run is not None else None


async def _set_run_state(
    run_id: UUID,
    status: str,
    *,
    failure_message: str | None = None,
) -> None:
    async with SessionFactory() as session:
        run = await session.get(EvaluationRun, run_id)
        if run is None:
            return
        run.status = status
        if status == "running" and run.started_at is None:
            run.started_at = utc_now()
        if status in {"completed", "cancelled", "failed"}:
            run.finished_at = utc_now()
        if failure_message is not None:
            run.failure_message = failure_message[:2000]
        await session.commit()


async def _pending_items(run_id: UUID) -> list[EvaluationItem]:
    async with SessionFactory() as session:
        result = await session.execute(
            select(EvaluationItem)
            .where(
                EvaluationItem.run_id == run_id,
                EvaluationItem.status.in_(("pending", "running")),
            )
            .order_by(EvaluationItem.id)
        )
        items = list(result.scalars())
        recovered = False
        for item in items:
            if item.status == "running":
                item.status = "pending"
                recovered = True
        if recovered:
            await session.commit()
        return items


async def _mark_items_running(run_id: UUID, item_ids: list[int]) -> None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(EvaluationItem).where(
                EvaluationItem.run_id == run_id,
                EvaluationItem.id.in_(item_ids),
                EvaluationItem.status == "pending",
            )
        )
        for item in result.scalars():
            item.status = "running"
        await session.commit()


async def _score_one(
    *,
    item: EvaluationItem,
    question: EvaluationQuestion,
    run: EvaluationRun,
    api_key: str,
    client: httpx.AsyncClient,
    chart_cache: dict[str, BaziChartToolResult],
) -> ItemOutcome:
    try:
        user_prompt, _, prompt_sha256 = build_evaluation_prompt(question)
    except Exception as exc:
        return ItemOutcome(
            item_id=item.id,
            predicted_answer=None,
            is_correct=False,
            status="error",
            confidence=None,
            reasoning_summary=None,
            error_message=f"评测上下文构建失败：{exc}",
            latency_ms=None,
            input_tokens=0,
            output_tokens=0,
            prompt_sha256=None,
            agent_trace=None,
        )

    last_error: EvaluationModelError | None = None
    prior_input_tokens = 0
    prior_output_tokens = 0
    prior_latency_ms = 0
    for attempt in range(3):
        try:
            result = await request_evaluation_answer(
                run=run,
                api_key=api_key,
                user_prompt=user_prompt,
                question=question,
                client=client,
                chart_cache=chart_cache,
            )
            predicted = result.answer.answer
            return ItemOutcome(
                item_id=item.id,
                predicted_answer=predicted,
                is_correct=predicted == item.correct_answer,
                status="completed",
                confidence=result.answer.confidence,
                reasoning_summary=result.answer.reasoning_summary,
                error_message=None,
                latency_ms=prior_latency_ms + result.latency_ms,
                input_tokens=prior_input_tokens + result.input_tokens,
                output_tokens=prior_output_tokens + result.output_tokens,
                prompt_sha256=prompt_sha256,
                agent_trace=result.agent_trace,
            )
        except EvaluationModelError as exc:
            last_error = exc
            prior_input_tokens += exc.input_tokens
            prior_output_tokens += exc.output_tokens
            prior_latency_ms += exc.latency_ms or 0
            if not exc.retryable or attempt == 2:
                break
            await asyncio.sleep(2**attempt)

    assert last_error is not None
    return ItemOutcome(
        item_id=item.id,
        predicted_answer=None,
        is_correct=False,
        status="error",
        confidence=None,
        reasoning_summary=None,
        error_message=str(last_error),
        latency_ms=prior_latency_ms or None,
        input_tokens=prior_input_tokens,
        output_tokens=prior_output_tokens,
        prompt_sha256=prompt_sha256,
        agent_trace=last_error.agent_trace,
        fatal=last_error.fatal,
    )


async def _persist_outcomes(run_id: UUID, outcomes: list[ItemOutcome]) -> None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(EvaluationItem).where(
                EvaluationItem.run_id == run_id,
                EvaluationItem.id.in_([outcome.item_id for outcome in outcomes]),
            )
        )
        items = {item.id: item for item in result.scalars()}
        for outcome in outcomes:
            item = items.get(outcome.item_id)
            if item is None or item.status != "running":
                continue
            item.predicted_answer = outcome.predicted_answer
            item.is_correct = outcome.is_correct
            item.status = outcome.status
            item.confidence = outcome.confidence
            item.reasoning_summary = outcome.reasoning_summary
            item.error_message = outcome.error_message
            item.latency_ms = outcome.latency_ms
            item.input_tokens = outcome.input_tokens
            item.output_tokens = outcome.output_tokens
            item.prompt_sha256 = outcome.prompt_sha256
            item.agent_trace = outcome.agent_trace

        run = await session.get(EvaluationRun, run_id)
        if run is None:
            return
        completed = await session.scalar(
            select(func.count())
            .select_from(EvaluationItem)
            .where(
                EvaluationItem.run_id == run_id,
                EvaluationItem.status.in_(("completed", "error")),
            )
        )
        correct = await session.scalar(
            select(func.count())
            .select_from(EvaluationItem)
            .where(EvaluationItem.run_id == run_id, EvaluationItem.is_correct.is_(True))
        )
        errors = await session.scalar(
            select(func.count())
            .select_from(EvaluationItem)
            .where(EvaluationItem.run_id == run_id, EvaluationItem.status == "error")
        )
        input_tokens = await session.scalar(
            select(func.coalesce(func.sum(EvaluationItem.input_tokens), 0)).where(
                EvaluationItem.run_id == run_id
            )
        )
        output_tokens = await session.scalar(
            select(func.coalesce(func.sum(EvaluationItem.output_tokens), 0)).where(
                EvaluationItem.run_id == run_id
            )
        )
        run.completed_questions = int(completed or 0)
        run.correct_answers = int(correct or 0)
        run.error_count = int(errors or 0)
        run.input_tokens = int(input_tokens or 0)
        run.output_tokens = int(output_tokens or 0)
        await session.commit()


async def execute_evaluation_run(run_id: UUID) -> None:
    dataset = load_dataset()
    try:
        run, api_key = await _credential_and_run(run_id)
    except Exception as exc:
        await _set_run_state(run_id, "failed", failure_message=str(exc))
        return
    if dataset.sha256 != run.dataset_sha256:
        await _set_run_state(
            run_id,
            "failed",
            failure_message="本地数据集 SHA-256 已变化，拒绝使用不同版本继续评测",
        )
        return
    if run.status == "cancel_requested":
        await _set_run_state(run_id, "cancelled")
        return
    await _set_run_state(run_id, "running")
    pending = await _pending_items(run_id)
    if not pending:
        await _set_run_state(run_id, "completed")
        return

    chart_cache: dict[str, BaziChartToolResult] = {}
    try:
        async with httpx.AsyncClient(timeout=model_timeout()) as client:
            for start in range(0, len(pending), run.max_concurrency):
                if await _run_status(run_id) == "cancel_requested":
                    await _set_run_state(run_id, "cancelled")
                    return
                batch = pending[start : start + run.max_concurrency]
                await _mark_items_running(run_id, [item.id for item in batch])
                outcomes = await asyncio.gather(
                    *[
                        _score_one(
                            item=item,
                            question=dataset.get_question(item.question_id),
                            run=run,
                            api_key=api_key,
                            client=client,
                            chart_cache=chart_cache,
                        )
                        for item in batch
                    ]
                )
                await _persist_outcomes(run_id, outcomes)
                fatal = next((outcome for outcome in outcomes if outcome.fatal), None)
                if fatal is not None:
                    await _set_run_state(
                        run_id,
                        "failed",
                        failure_message=fatal.error_message or "模型服务配置错误",
                    )
                    return
        await _set_run_state(run_id, "completed")
    except asyncio.CancelledError:
        await _set_run_state(run_id, "queued")
        raise
    except Exception as exc:
        await _set_run_state(run_id, "failed", failure_message=f"后台评测失败：{exc}")


class EvaluationTaskManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._queued: set[UUID] = set()
        self._worker_task: asyncio.Task[None] | None = None

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def start(self) -> None:
        self._ensure_worker()
        async with SessionFactory() as session:
            result = await session.execute(
                select(EvaluationRun)
                .where(EvaluationRun.status.in_(ACTIVE_RUN_STATUSES))
                .order_by(EvaluationRun.created_at)
            )
            runs = list(result.scalars())
            for run in runs:
                if run.status == "running":
                    run.status = "queued"
            await session.commit()
        for run in runs:
            await self.enqueue(run.id)

    async def enqueue(self, run_id: UUID) -> None:
        self._ensure_worker()
        if run_id in self._queued:
            return
        self._queued.add(run_id)
        await self._queue.put(run_id)

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    async def _worker_loop(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await execute_evaluation_run(run_id)
            except Exception as exc:
                await _set_run_state(
                    run_id,
                    "failed",
                    failure_message=f"后台评测失败：{exc}",
                )
            finally:
                self._queued.discard(run_id)
                self._queue.task_done()


evaluation_task_manager = EvaluationTaskManager()

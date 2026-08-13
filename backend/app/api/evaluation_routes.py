"""Administrator-only MingLi evaluation endpoints."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from copy import deepcopy
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from ..auth import AdminUserDependency, AuthRepositoryDependency, request_ip
from ..bazi.engine import ENGINE_VERSION
from ..bazi.policy import DEFAULT_POLICY
from ..credentials import ModelCredentialRepository, get_credential_repository
from ..evals.mingli_bench.context import PROMPT_VERSION
from ..evals.mingli_bench.dataset import (
    AVAILABLE_YEARS,
    DATASET_NAME,
    DatasetUnavailableError,
    EvaluationQuestion,
    load_dataset,
)
from ..evals.mingli_bench.repository import EvaluationRepositoryDependency
from ..evals.mingli_bench.schemas import (
    EvaluationBreakdown,
    EvaluationDatasetOverview,
    EvaluationItemList,
    EvaluationItemResponse,
    EvaluationItemTraceResponse,
    EvaluationModelCallTrace,
    EvaluationOptionResponse,
    EvaluationOverview,
    EvaluationRunDetail,
    EvaluationRunList,
    EvaluationRunSummary,
    EvaluationStartRequest,
    EvaluationTraceStep,
    StoredEvaluationAgentTrace,
)
from ..evals.mingli_bench.worker import evaluation_task_manager
from ..models import EvaluationItem, EvaluationRun

router = APIRouter(prefix="/api/v1/admin/evaluations", tags=["admin-evaluations"])
CredentialRepositoryDependency = Annotated[
    ModelCredentialRepository,
    Depends(get_credential_repository),
]


def _accuracy(correct: int, completed: int) -> float | None:
    return round(correct / completed, 4) if completed else None


def _run_summary(run: EvaluationRun) -> EvaluationRunSummary:
    return EvaluationRunSummary(
        id=run.id,
        scope=run.scope,
        benchmark_year=run.benchmark_year,
        mode=run.mode,
        max_concurrency=run.max_concurrency,
        dataset_name=run.dataset_name,
        dataset_sha256=run.dataset_sha256,
        dataset_question_count=run.dataset_question_count,
        provider=run.provider,
        api_protocol=run.api_protocol,
        model=run.model,
        prompt_version=run.prompt_version,
        engine_version=run.engine_version,
        calculation_policy_version=run.calculation_policy_version,
        status=run.status,
        total_questions=run.total_questions,
        completed_questions=run.completed_questions,
        correct_answers=run.correct_answers,
        error_count=run.error_count,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        progress=round(run.completed_questions / run.total_questions, 4),
        accuracy=_accuracy(run.correct_answers, run.completed_questions),
        started_at=run.started_at,
        finished_at=run.finished_at,
        failure_message=run.failure_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _breakdown(
    items: list[EvaluationItem],
    key: Callable[[EvaluationItem], str],
) -> list[EvaluationBreakdown]:
    groups: dict[str, list[EvaluationItem]] = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    values = []
    for name in sorted(groups):
        group = groups[name]
        finished = [item for item in group if item.status in {"completed", "error"}]
        correct = sum(item.is_correct for item in finished)
        errors = sum(item.status == "error" for item in finished)
        values.append(
            EvaluationBreakdown(
                key=name,
                total=len(group),
                completed=len(finished),
                correct=correct,
                errors=errors,
                accuracy=_accuracy(correct, len(finished)),
            )
        )
    return values


def _run_detail(run: EvaluationRun, items: list[EvaluationItem]) -> EvaluationRunDetail:
    summary = _run_summary(run).model_dump()
    return EvaluationRunDetail(
        **summary,
        by_year=_breakdown(items, lambda item: str(item.benchmark_year)),
        by_category=_breakdown(items, lambda item: item.category),
    )


def _item_response(item: EvaluationItem, question: EvaluationQuestion) -> EvaluationItemResponse:
    return EvaluationItemResponse(
        id=item.id,
        question_id=item.question_id,
        case_id=item.case_id,
        benchmark_year=item.benchmark_year,
        category=item.category,
        question=question.question,
        options=[
            EvaluationOptionResponse(letter=option.letter, text=option.text)
            for option in question.options
        ],
        correct_answer=item.correct_answer,
        predicted_answer=item.predicted_answer,
        is_correct=item.is_correct,
        status=item.status,
        confidence=item.confidence,
        reasoning_summary=item.reasoning_summary,
        error_message=item.error_message,
        latency_ms=item.latency_ms,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
        prompt_sha256=item.prompt_sha256,
    )


def _next_request_body(
    current: dict,
    response: dict,
    *,
    api_protocol: str,
    tool_executions: list,
) -> dict:
    next_body = deepcopy(current)
    if api_protocol == "responses":
        response_output = response.get("output")
        continuation = deepcopy(response_output) if isinstance(response_output, list) else []
        request_input = next_body.setdefault("input", [])
        request_input.extend(continuation)
        function_calls = [
            value
            for value in continuation
            if isinstance(value, dict) and value.get("type") == "function_call"
        ]
        for call, execution in zip(function_calls, tool_executions, strict=False):
            request_input.append(
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": json.dumps(
                        execution.output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        return next_body

    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
    messages = next_body.setdefault("messages", [])
    messages.append(
        {
            "role": "assistant",
            "content": message.get("content") if isinstance(message, dict) else None,
            "tool_calls": deepcopy(tool_calls),
        }
    )
    for call, execution in zip(tool_calls, tool_executions, strict=False):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id") if isinstance(call, dict) else None,
                "name": execution.name,
                "content": json.dumps(
                    execution.output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return next_body


def _action_tool_call_count(response: dict, api_protocol: str) -> int:
    if api_protocol == "responses":
        output = response.get("output")
        if not isinstance(output, list):
            return 0
        return sum(
            isinstance(value, dict) and value.get("type") == "function_call"
            for value in output
        )
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
    return len(tool_calls) if isinstance(tool_calls, list) else 0


def _trace_model_calls(
    trace: StoredEvaluationAgentTrace,
    *,
    api_protocol: str,
) -> list[EvaluationModelCallTrace]:
    model_calls: list[EvaluationModelCallTrace] = []
    request_body = deepcopy(trace.initial_request_body)
    tool_index = 0
    for stored_call in trace.model_calls:
        tool_call_count = stored_call.tool_call_count
        if stored_call.stage == "action_selection" and tool_call_count == 0:
            tool_call_count = _action_tool_call_count(
                stored_call.response_body,
                api_protocol,
            )
        model_calls.append(
            EvaluationModelCallTrace(
                sequence=stored_call.sequence,
                stage=stored_call.stage,
                request_body=deepcopy(request_body),
                response_body=stored_call.response_body,
                duration_ms=stored_call.duration_ms,
                tool_call_count=tool_call_count,
            )
        )
        call_tools = trace.tool_executions[
            tool_index : tool_index + tool_call_count
        ]
        tool_index += len(call_tools)
        if stored_call.stage == "action_selection":
            request_body = _next_request_body(
                request_body,
                stored_call.response_body,
                api_protocol=api_protocol,
                tool_executions=call_tools,
            )
    return model_calls


def _trace_prompts(initial_request_body: dict, api_protocol: str) -> tuple[str | None, str | None]:
    if api_protocol == "responses":
        system_prompt = initial_request_body.get("instructions")
        request_input = initial_request_body.get("input")
        user_prompt = None
        if isinstance(request_input, list):
            user_prompt = next(
                (
                    value.get("content")
                    for value in request_input
                    if isinstance(value, dict)
                    and value.get("role") == "user"
                    and isinstance(value.get("content"), str)
                ),
                None,
            )
        return system_prompt if isinstance(system_prompt, str) else None, user_prompt

    messages = initial_request_body.get("messages")
    if not isinstance(messages, list):
        return None, None

    def content_for(role: str) -> str | None:
        contents = [
            value["content"]
            for value in messages
            if isinstance(value, dict)
            and value.get("role") == role
            and isinstance(value.get("content"), str)
        ]
        return "\n\n".join(contents) if contents else None

    return content_for("system"), content_for("user")


def _item_trace(item: EvaluationItem, run: EvaluationRun) -> EvaluationItemTraceResponse:
    trace = (
        StoredEvaluationAgentTrace.model_validate(item.agent_trace)
        if item.agent_trace is not None
        else None
    )
    model_calls = (
        _trace_model_calls(trace, api_protocol=run.api_protocol) if trace is not None else []
    )
    tool_executions = list(trace.tool_executions) if trace is not None else []
    system_prompt, user_prompt = (
        _trace_prompts(trace.initial_request_body, run.api_protocol)
        if trace is not None
        else (None, None)
    )
    prompt_ready = item.prompt_sha256 is not None
    scored = item.status == "completed"
    score_detail = (
        f"模型选择 {item.predicted_answer}，标准答案为 {item.correct_answer}，"
        f"判定为{'正确' if item.is_correct else '错误'}。"
        if scored
        else item.error_message or "模型答案未通过解析和评分。"
    )
    steps = [
        EvaluationTraceStep(
            id="dataset",
            title="读取评测题目",
            category="deterministic",
            status="completed",
            detail="读取题面和选项；标准答案未进入 Agent 请求。",
        ),
        EvaluationTraceStep(
            id="prompt",
            title="组装工具调用任务",
            category="prompt",
            status="completed" if prompt_ready else "failed",
            detail=(
                "使用自然文本组合出生资料、题目和选项，并完成答案泄漏检查。"
                if prompt_ready
                else item.error_message or "评测提示词构造失败。"
            ),
        ),
    ]
    tool_index = 0
    for call in model_calls:
        if call.stage == "action_selection":
            tool_call_count = call.tool_call_count
            if tool_call_count == 0 and tool_index < len(tool_executions):
                tool_call_count = 1  # Compatibility with traces stored before this field existed.
            call_tool_executions = tool_executions[
                tool_index : tool_index + tool_call_count
            ]
            steps.append(
                EvaluationTraceStep(
                    id=f"action_{call.sequence}",
                    title=f"响应 {call.sequence} · Action",
                    category="model",
                    status="completed" if call_tool_executions else "failed",
                    detail=(
                        "模型发起工具调用："
                        + "、".join(item.name for item in call_tool_executions)
                        + "。"
                        if call_tool_executions
                        else item.error_message or "模型生成的工具调用无效。"
                    ),
                    duration_ms=call.duration_ms,
                )
            )
            for tool_execution in call_tool_executions:
                steps.extend(
                    [
                        EvaluationTraceStep(
                            id=f"tool_{tool_execution.sequence}",
                            title=f"执行工具 {tool_execution.sequence}",
                            category="tool",
                            status="completed",
                            detail=f"后端校验参数后执行 {tool_execution.name}。",
                            duration_ms=tool_execution.duration_ms,
                        ),
                        EvaluationTraceStep(
                            id=f"observation_{tool_execution.sequence}",
                            title=f"Observation {tool_execution.sequence}",
                            category="context",
                            status="completed",
                            detail="向下一轮响应返回该工具的确定性结果。",
                        ),
                    ]
                )
            tool_index += len(call_tool_executions)
            continue
        if call.stage == "final_answer":
            steps.append(
                EvaluationTraceStep(
                    id=f"final_{call.sequence}",
                    title=f"响应 {call.sequence} · Final",
                    category="model",
                    status="completed",
                    detail="模型结束工具调用循环并返回选择题答案。",
                    duration_ms=call.duration_ms,
                )
            )
            continue
        steps.append(
            EvaluationTraceStep(
                id=f"error_{call.sequence}",
                title=f"响应 {call.sequence} · 错误",
                category="model",
                status="failed",
                detail=item.error_message or "模型请求失败。",
                duration_ms=call.duration_ms,
            )
        )
    steps.append(
        EvaluationTraceStep(
            id="score",
            title="解析并评分",
            category="validation",
            status="completed" if scored else "failed",
            detail=score_detail,
        )
    )
    return EvaluationItemTraceResponse(
        question_id=item.question_id,
        status=item.status,
        steps=steps,
        api_protocol=run.api_protocol,
        model=run.model,
        endpoint=(
            f"{run.base_url.rstrip('/')}/responses"
            if run.api_protocol == "responses"
            else f"{run.base_url.rstrip('/')}/chat/completions"
        ),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_calls=model_calls,
        tool_executions=tool_executions,
        prompt_sha256=item.prompt_sha256,
        redacted=["API 密钥", "Authorization 请求头", "模型内部推理文本"],
    )


@router.get("/overview", response_model=EvaluationOverview)
async def evaluation_overview(
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    credential_repository: CredentialRepositoryDependency,
) -> EvaluationOverview:
    credential = await credential_repository.get()
    active = await repository.active_run()
    try:
        dataset = load_dataset()
        year_counts = {
            str(year): sum(question.benchmark_year == year for question in dataset.questions)
            for year in AVAILABLE_YEARS
        }
        dataset_overview = EvaluationDatasetOverview(
            available=True,
            dataset_name=DATASET_NAME,
            sha256=dataset.sha256,
            question_count=len(dataset.questions),
            case_count=len({question.case_id for question in dataset.questions}),
            years=year_counts,
            scopes={"quick": 5, "year": 40, "all": len(dataset.questions)},
        )
    except DatasetUnavailableError as exc:
        dataset_overview = EvaluationDatasetOverview(
            available=False,
            error=str(exc),
            dataset_name=DATASET_NAME,
        )
    return EvaluationOverview(
        dataset=dataset_overview,
        model_configured=credential is not None,
        model=credential.model if credential else None,
        api_protocol=credential.api_protocol if credential else None,
        active_run=_run_summary(active) if active else None,
    )


@router.get("/runs", response_model=EvaluationRunList)
async def list_evaluation_runs(
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    limit: int = Query(default=20, ge=1, le=100),
) -> EvaluationRunList:
    runs, total = await repository.list_runs(limit=limit)
    return EvaluationRunList(items=[_run_summary(run) for run in runs], total=total)


@router.post(
    "/runs",
    response_model=EvaluationRunDetail,
    status_code=status.HTTP_201_CREATED,
)
async def start_evaluation_run(
    payload: EvaluationStartRequest,
    request: Request,
    admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    credential_repository: CredentialRepositoryDependency,
    auth_repository: AuthRepositoryDependency,
) -> EvaluationRunDetail:
    if await repository.active_run() is not None:
        raise HTTPException(status_code=409, detail="已有评测正在运行，请等待完成或先取消")
    credential = await credential_repository.get()
    if credential is None:
        raise HTTPException(status_code=409, detail="请先配置并测试模型 API")
    try:
        dataset = load_dataset()
        questions = dataset.select_questions(
            scope=payload.scope,
            benchmark_year=payload.benchmark_year,
        )
    except DatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if payload.confirmed_request_count != len(questions):
        raise HTTPException(
            status_code=409,
            detail=f"确认请求数与实际题数不一致，实际将调用 {len(questions)} 次",
        )
    run = EvaluationRun(
        created_by_user_id=admin.id,
        dataset_name=DATASET_NAME,
        dataset_sha256=dataset.sha256,
        dataset_question_count=len(dataset.questions),
        scope=payload.scope,
        benchmark_year=payload.benchmark_year,
        mode=payload.mode,
        max_concurrency=payload.max_concurrency,
        provider=credential.provider,
        api_protocol=credential.api_protocol,
        model=credential.model,
        base_url=credential.base_url,
        prompt_version=PROMPT_VERSION,
        engine_version=ENGINE_VERSION,
        calculation_policy_version=DEFAULT_POLICY.version,
        status="queued",
        total_questions=len(questions),
    )
    await repository.create_run(run=run, questions=questions, dataset=dataset)
    await auth_repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=None,
        action="admin.evaluation_started",
        details={
            "run_id": str(run.id),
            "scope": run.scope,
            "benchmark_year": run.benchmark_year,
            "request_count": run.total_questions,
        },
        ip_address=request_ip(request),
    )
    await evaluation_task_manager.enqueue(run.id)
    items = await repository.all_items(run.id)
    return _run_detail(run, items)


@router.get("/runs/{run_id}", response_model=EvaluationRunDetail)
async def get_evaluation_run(
    run_id: UUID,
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
) -> EvaluationRunDetail:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    return _run_detail(run, await repository.all_items(run.id))


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluation_run(
    run_id: UUID,
    request: Request,
    admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    auth_repository: AuthRepositoryDependency,
) -> Response:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    if run.status in {"queued", "running", "cancel_requested"}:
        raise HTTPException(status_code=409, detail="运行中的评测不能删除，请先取消并等待结束")
    audit_details = {
        "run_id": str(run.id),
        "scope": run.scope,
        "benchmark_year": run.benchmark_year,
        "status": run.status,
        "total_questions": run.total_questions,
    }
    await repository.delete_run(run)
    await auth_repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=None,
        action="admin.evaluation_deleted",
        details=audit_details,
        ip_address=request_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs/{run_id}/items", response_model=EvaluationItemList)
async def list_evaluation_items(
    run_id: UUID,
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
    result: str | None = Query(default=None, pattern="^(correct|incorrect|error)$"),
) -> EvaluationItemList:
    if await repository.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    items, total = await repository.list_items(
        run_id,
        offset=offset,
        limit=limit,
        result_filter=result,
    )
    try:
        dataset = load_dataset()
    except DatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EvaluationItemList(
        items=[_item_response(item, dataset.get_question(item.question_id)) for item in items],
        total=total,
    )


@router.get(
    "/runs/{run_id}/items/{item_id}/trace",
    response_model=EvaluationItemTraceResponse,
)
async def get_evaluation_item_trace(
    run_id: UUID,
    item_id: int,
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
) -> EvaluationItemTraceResponse:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    item = await repository.get_item(run_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="评测题目不存在")
    if item.status in {"pending", "running"}:
        detail = (
            "该题正在调用模型，执行链路将在完成后提供"
            if item.status == "running"
            else "该题尚未开始"
        )
        raise HTTPException(status_code=409, detail=detail)
    return _item_trace(item, run)


@router.get("/runs/{run_id}/export")
async def export_evaluation_run(
    run_id: UUID,
    _admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    format: str = Query(pattern="^(json|csv)$"),
) -> Response:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    try:
        dataset = load_dataset()
    except DatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    items = await repository.all_items(run_id)
    serialized_items = [
        _item_response(item, dataset.get_question(item.question_id)).model_dump(mode="json")
        for item in items
    ]
    filename = f"mingli-evaluation-{run_id}"
    if format == "json":
        payload = {
            "run": _run_detail(run, items).model_dump(mode="json"),
            "items": serialized_items,
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "question_id",
            "case_id",
            "benchmark_year",
            "category",
            "question",
            "predicted_answer",
            "correct_answer",
            "is_correct",
            "status",
            "confidence",
            "reasoning_summary",
            "error_message",
            "latency_ms",
            "input_tokens",
            "output_tokens",
        ),
    )
    writer.writeheader()
    for item in serialized_items:
        writer.writerow({field: item.get(field) for field in writer.fieldnames})
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


@router.post("/runs/{run_id}/cancel", response_model=EvaluationRunDetail)
async def cancel_evaluation_run(
    run_id: UUID,
    request: Request,
    admin: AdminUserDependency,
    repository: EvaluationRepositoryDependency,
    auth_repository: AuthRepositoryDependency,
) -> EvaluationRunDetail:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    run = await repository.request_cancel(run)
    await auth_repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=None,
        action="admin.evaluation_cancel_requested",
        details={"run_id": str(run.id)},
        ip_address=request_ip(request),
    )
    return _run_detail(run, await repository.all_items(run.id))

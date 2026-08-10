"""One-shot structured model call for MingLi multiple-choice questions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from ...models import EvaluationRun
from .context import SYSTEM_PROMPT

MODEL_TIMEOUT_SECONDS = 90.0
ANSWER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning_summary": {"type": "string"},
    },
    "required": ["answer", "confidence", "reasoning_summary"],
    "additionalProperties": False,
}


class EvaluationAnswer(BaseModel):
    answer: str = Field(pattern="^[A-D]$")
    confidence: int = Field(ge=0, le=100)
    reasoning_summary: str = Field(min_length=1, max_length=500)


class EvaluationModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        fatal: bool = False,
        request_snapshot: dict[str, Any] | None = None,
        response_status_code: int | None = None,
        raw_response: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal
        self.request_snapshot = request_snapshot
        self.response_status_code = response_status_code
        self.raw_response = raw_response
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class EvaluationModelResult:
    answer: EvaluationAnswer
    request_snapshot: dict[str, Any]
    response_status_code: int
    raw_response: dict[str, Any]
    latency_ms: int
    input_tokens: int
    output_tokens: int


def _responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for output in payload.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise EvaluationModelError("模型拒绝回答该评测题")
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise EvaluationModelError("模型没有返回可用的评测答案")


def _chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise EvaluationModelError("模型没有返回可用的评测答案")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise EvaluationModelError("模型没有返回可用的评测答案")
    return content


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model answer is not an object")
    return value


def _usage(payload: dict[str, Any], protocol: str) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _raw_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {"unparsed_response": response.text[:50_000]}
    return payload if isinstance(payload, dict) else {"response_body": payload}


async def request_evaluation_answer(
    *,
    run: EvaluationRun,
    api_key: str,
    user_prompt: str,
    client: httpx.AsyncClient,
) -> EvaluationModelResult:
    if run.api_protocol == "responses":
        url = f"{run.base_url.rstrip('/')}/responses"
        body = {
            "model": run.model,
            "instructions": SYSTEM_PROMPT,
            "input": user_prompt,
            "max_output_tokens": 400,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "mingli_evaluation_answer",
                    "strict": True,
                    "schema": ANSWER_JSON_SCHEMA,
                }
            },
        }
    elif run.api_protocol == "chat_completions":
        url = f"{run.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": run.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                    + "\n只输出 JSON 对象，不要输出 Markdown 或额外文字。",
                },
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 400,
            "stream": False,
        }
    else:
        raise EvaluationModelError("评测运行使用了不支持的模型协议", fatal=True)

    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    request_snapshot = {
        "method": "POST",
        "endpoint": url,
        "provider": run.provider,
        "api_protocol": run.api_protocol,
        "model": run.model,
        "headers": {
            "Authorization": "Bearer [REDACTED]",
            "Content-Type": "application/json",
        },
        "body": body,
    }

    started = perf_counter()
    try:
        response = await client.post(
            url,
            headers=request_headers,
            json=body,
        )
    except httpx.TimeoutException as exc:
        raise EvaluationModelError(
            "模型服务响应超时",
            retryable=True,
            request_snapshot=request_snapshot,
            latency_ms=round((perf_counter() - started) * 1000),
        ) from exc
    except httpx.RequestError as exc:
        raise EvaluationModelError(
            "无法连接模型服务",
            retryable=True,
            request_snapshot=request_snapshot,
            latency_ms=round((perf_counter() - started) * 1000),
        ) from exc
    latency_ms = round((perf_counter() - started) * 1000)
    raw_response = _raw_response(response)
    if response.status_code in {401, 403}:
        raise EvaluationModelError(
            "模型服务鉴权失败",
            fatal=True,
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )
    if response.status_code == 404:
        raise EvaluationModelError(
            "模型接口或模型不存在",
            fatal=True,
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )
    if response.status_code == 429 or response.status_code >= 500:
        raise EvaluationModelError(
            f"模型服务暂时不可用（HTTP {response.status_code}）",
            retryable=True,
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )
    if not response.is_success:
        raise EvaluationModelError(
            f"模型调用失败（HTTP {response.status_code}）",
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    try:
        payload = raw_response
        if not isinstance(payload, dict):
            raise ValueError("response is not an object")
        output_text = (
            _responses_text(payload)
            if run.api_protocol == "responses"
            else _chat_text(payload)
        )
        answer = EvaluationAnswer.model_validate(_json_object(output_text))
    except EvaluationModelError as exc:
        raise EvaluationModelError(
            str(exc),
            retryable=exc.retryable,
            fatal=exc.fatal,
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        ) from exc
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise EvaluationModelError(
            "模型返回的答案结构不符合约定",
            request_snapshot=request_snapshot,
            response_status_code=response.status_code,
            raw_response=raw_response,
            latency_ms=latency_ms,
        ) from exc
    input_tokens, output_tokens = _usage(payload, run.api_protocol)
    return EvaluationModelResult(
        answer=answer,
        request_snapshot=request_snapshot,
        response_status_code=response.status_code,
        raw_response=payload,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def model_timeout() -> httpx.Timeout:
    return httpx.Timeout(MODEL_TIMEOUT_SECONDS, connect=15.0)

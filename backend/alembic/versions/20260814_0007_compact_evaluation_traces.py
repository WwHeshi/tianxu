"""Compact per-item evaluation Agent traces.

Revision ID: 20260814_0007
Revises: 20260811_0006
Create Date: 2026-08-14
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0007"
down_revision: str | None = "20260811_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _compact_trace(
    snapshot: Any,
    *,
    legacy_response: Any = None,
    latency_ms: int | None = None,
    item_status: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    old_calls = snapshot.get("model_calls")
    calls = old_calls if isinstance(old_calls, list) else []
    first_call = calls[0] if calls and isinstance(calls[0], dict) else {}
    initial_request_body = first_call.get("request_body", snapshot.get("body"))
    if not isinstance(initial_request_body, dict):
        return None

    compact_calls = []
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            continue
        response_body = call.get("response_body")
        compact_calls.append(
            {
                "sequence": call.get("sequence", index),
                "stage": call.get("stage", "error"),
                "response_body": response_body if isinstance(response_body, dict) else {},
                "duration_ms": call.get("duration_ms", 0),
                "tool_call_count": call.get(
                    "tool_call_count",
                    1 if call.get("stage") == "action_selection" else 0,
                ),
            }
        )
    if not compact_calls and isinstance(legacy_response, dict):
        compact_calls.append(
            {
                "sequence": 1,
                "stage": "final_answer" if item_status == "completed" else "error",
                "response_body": legacy_response,
                "duration_ms": latency_ms or 0,
                "tool_call_count": 0,
            }
        )

    executions = snapshot.get("tool_executions")
    return {
        "initial_request_body": initial_request_body,
        "model_calls": compact_calls,
        "tool_executions": executions if isinstance(executions, list) else [],
    }


def upgrade() -> None:
    op.add_column("evaluation_items", sa.Column("agent_trace", sa.JSON(), nullable=True))
    connection = op.get_bind()
    items = sa.table(
        "evaluation_items",
        sa.column("id", sa.Integer()),
        sa.column("request_snapshot", sa.JSON()),
        sa.column("raw_response", sa.JSON()),
        sa.column("latency_ms", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("agent_trace", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(
            items.c.id,
            items.c.request_snapshot,
            items.c.raw_response,
            items.c.latency_ms,
            items.c.status,
        )
    )
    for item_id, snapshot, raw_response, latency_ms, item_status in rows:
        compact = _compact_trace(
            snapshot,
            legacy_response=raw_response,
            latency_ms=latency_ms,
            item_status=item_status,
        )
        if compact is not None:
            connection.execute(
                items.update().where(items.c.id == item_id).values(agent_trace=compact)
            )
    op.drop_column("evaluation_items", "raw_response")
    op.drop_column("evaluation_items", "response_status_code")
    op.drop_column("evaluation_items", "request_snapshot")


def downgrade() -> None:
    op.add_column("evaluation_items", sa.Column("request_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "evaluation_items",
        sa.Column("response_status_code", sa.Integer(), nullable=True),
    )
    op.add_column("evaluation_items", sa.Column("raw_response", sa.JSON(), nullable=True))
    connection = op.get_bind()
    items = sa.table(
        "evaluation_items",
        sa.column("id", sa.Integer()),
        sa.column("agent_trace", sa.JSON()),
        sa.column("request_snapshot", sa.JSON()),
    )
    rows = connection.execute(sa.select(items.c.id, items.c.agent_trace))
    for item_id, trace in rows:
        if isinstance(trace, dict):
            connection.execute(
                items.update().where(items.c.id == item_id).values(request_snapshot=trace)
            )
    op.drop_column("evaluation_items", "agent_trace")

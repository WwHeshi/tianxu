"""Add durable MingLi evaluation runs and items.

Revision ID: 20260811_0005
Revises: 20260810_0004
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0005"
down_revision: str | None = "20260810_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("dataset_question_count", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("benchmark_year", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), server_default="2", nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_protocol", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("calculation_policy_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("completed_questions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("correct_answers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_created_by_user_id",
        "evaluation_runs",
        ["created_by_user_id"],
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_created_at", "evaluation_runs", ["created_at"])

    op.create_table(
        "evaluation_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("benchmark_year", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("correct_answer", sa.String(length=1), nullable=False),
        sa.Column("predicted_answer", sa.String(length=1), nullable=True),
        sa.Column("is_correct", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "question_id", name="uq_evaluation_items_run_question"
        ),
    )
    op.create_index("ix_evaluation_items_run_id", "evaluation_items", ["run_id"])
    op.create_index("ix_evaluation_items_question_id", "evaluation_items", ["question_id"])
    op.create_index("ix_evaluation_items_case_id", "evaluation_items", ["case_id"])
    op.create_index("ix_evaluation_items_benchmark_year", "evaluation_items", ["benchmark_year"])
    op.create_index("ix_evaluation_items_category", "evaluation_items", ["category"])
    op.create_index("ix_evaluation_items_status", "evaluation_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_items_status", table_name="evaluation_items")
    op.drop_index("ix_evaluation_items_category", table_name="evaluation_items")
    op.drop_index("ix_evaluation_items_benchmark_year", table_name="evaluation_items")
    op.drop_index("ix_evaluation_items_case_id", table_name="evaluation_items")
    op.drop_index("ix_evaluation_items_question_id", table_name="evaluation_items")
    op.drop_index("ix_evaluation_items_run_id", table_name="evaluation_items")
    op.drop_table("evaluation_items")
    op.drop_index("ix_evaluation_runs_created_at", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_status", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_created_by_user_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

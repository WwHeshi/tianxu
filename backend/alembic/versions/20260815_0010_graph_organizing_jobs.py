"""Add durable automatic graph organizing jobs.

Revision ID: 20260815_0010
Revises: 20260815_0009
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0010"
down_revision: str | None = "20260815_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_organizing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_title", sa.String(length=200), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_protocol", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("total_sections", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_sections", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_offset", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rules_extracted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rules_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rules_merged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conditions_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("relations_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conflicts_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ignored_sections", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_graph_organizing_jobs_document_id",
        "graph_organizing_jobs",
        ["document_id"],
    )
    op.create_index(
        "ix_graph_organizing_jobs_status",
        "graph_organizing_jobs",
        ["status"],
    )
    op.create_index(
        "ix_graph_organizing_jobs_created_at",
        "graph_organizing_jobs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_organizing_jobs_created_at", table_name="graph_organizing_jobs")
    op.drop_index("ix_graph_organizing_jobs_status", table_name="graph_organizing_jobs")
    op.drop_index("ix_graph_organizing_jobs_document_id", table_name="graph_organizing_jobs")
    op.drop_table("graph_organizing_jobs")

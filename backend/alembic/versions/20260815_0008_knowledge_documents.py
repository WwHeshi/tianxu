"""Store administrator-uploaded TXT knowledge documents.

Revision ID: 20260815_0008
Revises: 20260814_0007
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0008"
down_revision: str | None = "20260814_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("encoding", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_documents_title", "knowledge_documents", ["title"])
    op.create_index(
        "ix_knowledge_documents_sha256",
        "knowledge_documents",
        ["sha256"],
        unique=True,
    )
    op.create_index(
        "ix_knowledge_documents_created_at",
        "knowledge_documents",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_created_at", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_sha256", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_title", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

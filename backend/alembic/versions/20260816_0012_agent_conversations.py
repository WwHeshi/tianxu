"""Add user-owned Agent conversations.

Revision ID: 20260816_0012
Revises: 20260815_0011
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0012"
down_revision: str | None = "20260815_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=100), server_default="新对话", nullable=False),
        sa.Column("birth_input", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_conversations_user_updated",
        "agent_conversations",
        ["user_id", "updated_at"],
    )
    op.create_table(
        "agent_conversation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_conversation_messages_conversation_id",
        "agent_conversation_messages",
        ["conversation_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_conversation_messages_conversation_id",
        table_name="agent_conversation_messages",
    )
    op.drop_table("agent_conversation_messages")
    op.drop_index(
        "ix_agent_conversations_user_updated",
        table_name="agent_conversations",
    )
    op.drop_table("agent_conversations")

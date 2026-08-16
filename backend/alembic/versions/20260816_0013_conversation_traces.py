"""Add compact traces to Agent conversation messages.

Revision ID: 20260816_0013
Revises: 20260816_0012
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0013"
down_revision: str | None = "20260816_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_conversation_messages",
        sa.Column("agent_trace", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_conversation_messages", "agent_trace")

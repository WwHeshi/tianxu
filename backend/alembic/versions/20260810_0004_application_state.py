"""Add durable application state for one-time administrator bootstrap.

Revision ID: 20260810_0004
Revises: 20260810_0003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0004"
down_revision: str | None = "20260810_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("boolean_value", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO application_state (key, boolean_value)
            SELECT 'auth.bootstrap_completed', EXISTS (SELECT 1 FROM users)
            """
        )
    )


def downgrade() -> None:
    op.drop_table("application_state")

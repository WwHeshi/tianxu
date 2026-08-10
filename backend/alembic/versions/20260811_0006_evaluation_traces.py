"""Add per-item evaluation request and response trace fields.

Revision ID: 20260811_0006
Revises: 20260811_0005
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0006"
down_revision: str | None = "20260811_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_items",
        sa.Column("request_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "evaluation_items",
        sa.Column("response_status_code", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evaluation_items", "response_status_code")
    op.drop_column("evaluation_items", "request_snapshot")

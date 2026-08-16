"""Remove mandatory password-change state from users.

Revision ID: 20260816_0014
Revises: 20260816_0013
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0014"
down_revision: str | None = "20260816_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "must_change_password")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

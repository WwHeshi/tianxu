"""Add model API protocol.

Revision ID: 20260808_0002
Revises: 20260808_0001
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260808_0002"
down_revision: str | None = "20260808_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_credentials",
        sa.Column(
            "api_protocol",
            sa.String(length=32),
            server_default="responses",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("model_credentials", "api_protocol")

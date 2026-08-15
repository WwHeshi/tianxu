"""Remove indexes duplicated by unique constraints.

Revision ID: 20260815_0009
Revises: 20260815_0008
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_0009"
down_revision: str | None = "20260815_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_model_credentials_scope", table_name="model_credentials")


def downgrade() -> None:
    op.create_index("ix_model_credentials_scope", "model_credentials", ["scope"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index("ix_users_username", "users", ["username"])

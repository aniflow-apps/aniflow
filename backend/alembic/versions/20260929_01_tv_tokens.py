"""The table of television tokens (build 181, 0.8.1).

Additive only: a new table, nothing existing is touched.

Revision ID: 20260929_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_01"
down_revision = "20260925_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tv_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tv_tokens")

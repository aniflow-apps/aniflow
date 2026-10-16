"""Per-anime automation flag.

Revision ID: 20260901_04
Revises: 20260901_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_04"
down_revision = "20260901_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Off for every existing anime: enabling automation is a decision, and an
    # upgrade must not start downloading on its own.
    op.add_column(
        "anime",
        sa.Column("automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("anime", "automation_enabled")

"""The libraries table (build 192, 0.8.5).

Additive only: a new table, nothing existing is touched. The system libraries
are seeded by services/libraries.py the first time they are read, not here, so
the migration does not need to know where the media root is mounted.

Revision ID: 20261002_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_02"
down_revision = "20261002_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "libraries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("folder", sa.String(length=500), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("fits_kinds", sa.String(length=200), nullable=True),
        sa.Column("fits_adult", sa.String(length=12), nullable=True),
        sa.Column("fits_censor", sa.String(length=12), nullable=True),
        sa.Column("fits_season", sa.String(length=12), nullable=True),
        sa.Column("shoko_folder_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("libraries")

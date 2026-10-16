"""The files a swap is replacing, kept until the new ones arrive (build 116).

A swap had two answers and both dealt with the old file at once - delete it,
or rename it out of the way - so between pressing the button and the new
release finishing there was nothing there. Eleven episodes in, that is eleven
gaps for as long as the new group takes, and if the new release does not
actually exist the gaps are permanent.

This table is the third answer: the old file stays where it is, its name is
written down here, and it is deleted once the replacement has been imported.
Empty is the normal state.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_02"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "superseded_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "anime_id",
            sa.String(length=36),
            sa.ForeignKey("anime.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            sa.String(length=36),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("subgroup", sa.String(length=80)),
        sa.Column("resolution", sa.String(length=16)),
        sa.Column("release_name", sa.String(length=300)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime()),
        sa.Column("error", sa.String(length=300)),
    )
    op.create_index("ix_superseded_files_anime_id", "superseded_files", ["anime_id"])
    op.create_index("ix_superseded_files_episode_id", "superseded_files", ["episode_id"])


def downgrade() -> None:
    op.drop_index("ix_superseded_files_episode_id", table_name="superseded_files")
    op.drop_index("ix_superseded_files_anime_id", table_name="superseded_files")
    op.drop_table("superseded_files")

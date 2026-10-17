"""Which library an anime lives in (build 192, 0.8.5).

One nullable column. Nothing is filled here: the migration cannot know where
the media root is mounted, and the default library does not exist until it is
first read. services/libraries.ensure_system() points every anime that has no
library at the default one the first time it runs, which is at startup.

Revision ID: 20261002_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_03"
down_revision = "20261002_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.add_column(sa.Column("library_id", sa.Integer(), nullable=True))
        batch.create_index("ix_anime_library_id", ["library_id"])
        batch.create_foreign_key(
            "fk_anime_library_id", "libraries", ["library_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.drop_constraint("fk_anime_library_id", type_="foreignkey")
        batch.drop_index("ix_anime_library_id")
        batch.drop_column("library_id")

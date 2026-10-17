"""Which episode an attention is about (build 90).

The table could point at an anime, a release, a download or a feed - and so a
record about one *file* had to be filed against its anime. That is too coarse
for two reasons.

Only one OPEN record exists per (kind, subject), so a season with four badly
encoded files produced one warning naming one of them. And the Activity page
reaches an episode by joining through the download row, which does not exist
for a record about a file already in the library: the row could not say which
episode it meant even when it knew.

Both are the same missing column. Nullable, because most kinds are not about
an episode at all - a feed that will not load is about a feed.

Revision ID: 20260911_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260911_01"
down_revision = "20260910_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite cannot add a foreign key to an existing table in place, so the
    # constraint is declared inside a batch operation - Alembic rebuilds the
    # table for SQLite and issues a plain ALTER everywhere else.
    with op.batch_alter_table("attention") as batch:
        batch.add_column(sa.Column("episode_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_attention_episode",
            "episodes",
            ["episode_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_attention_episode_id", "attention", ["episode_id"])


def downgrade() -> None:
    op.drop_index("ix_attention_episode_id", table_name="attention")
    with op.batch_alter_table("attention") as batch:
        batch.drop_constraint("fk_attention_episode", type_="foreignkey")
        batch.drop_column("episode_id")

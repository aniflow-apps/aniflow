"""A feed source can be owned by an indexer.

When an indexer's definition file supplies the site's feed URL, the indexer can
stand up an RSS source for it. That source is managed by the indexer: not
editable or deletable on its own, and removed with it - two records describing
the same site must not be able to drift apart.

Revision ID: 20260904_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260904_01"
down_revision = "20260903_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite cannot add a constraint to an existing table, so the batch form
    # rebuilds it - which is also how every other FK in this schema was added.
    with op.batch_alter_table("feed_sources") as batch:
        batch.add_column(sa.Column("indexer_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_feed_source_indexer", "indexers", ["indexer_id"], ["id"], ondelete="CASCADE",
        )
    op.create_index("ix_feed_sources_indexer_id", "feed_sources", ["indexer_id"])


def downgrade() -> None:
    op.drop_index("ix_feed_sources_indexer_id", table_name="feed_sources")
    with op.batch_alter_table("feed_sources") as batch:
        batch.drop_constraint("fk_feed_source_indexer", type_="foreignkey")
        batch.drop_column("indexer_id")

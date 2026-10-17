"""Which indexer an anime was chosen from (build 219).

One nullable column. Null is every anime that exists: they search the default
indexer, exactly as before. It is set when an anime is added from a release in
a particular indexer (Sukebei, say), so the searches for its later episodes ask
the site it came from rather than the default.

Revision ID: 20261008_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_01"
down_revision = "20261007_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.add_column(sa.Column("indexer_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_anime_indexer_id", "indexers", ["indexer_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.drop_constraint("fk_anime_indexer_id", type_="foreignkey")
        batch.drop_column("indexer_id")

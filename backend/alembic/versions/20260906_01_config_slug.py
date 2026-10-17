"""Indexers, feeds and subgroup profiles are backed by files.

Each of the three now mirrors a YAML file in the configuration folder, so
wiping the database no longer means setting them all up again. The column is
the link back to the file that owns the row - kept on the row rather than
derived from the file name, so renaming an object moves its file without
changing the id that anime profiles and managed feeds refer to.

Nothing is backfilled. An existing row has no file yet and is adopted by name
the first time its file appears, which is what the upgrade path in
``services/config_sync.py`` does; until then it behaves exactly as before.

Revision ID: 20260906_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260906_01"
down_revision = "20260905_01"
branch_labels = None
depends_on = None

TABLES = ("indexers", "feed_sources", "profile_templates")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("config_slug", sa.String(length=120), nullable=True))
        op.create_index(f"ix_{table}_config_slug", table, ["config_slug"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_config_slug", table_name=table)
        op.drop_column(table, "config_slug")

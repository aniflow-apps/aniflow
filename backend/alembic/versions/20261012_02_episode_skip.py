"""Episodes the user already has elsewhere (build 250).

One nullable column. Null is every episode that exists: they are wanted exactly as before. It is set when
an anime is added from the AniDB My List and the export says the episode is in the user's list, so the
anime does not start by fetching all twelve when eleven are already owned.

Revision ID: 20261012_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_02"
down_revision = "20261012_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.add_column(sa.Column("skip_reason", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.drop_column("skip_reason")

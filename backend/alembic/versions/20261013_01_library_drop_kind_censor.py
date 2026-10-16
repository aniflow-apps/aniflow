"""Drop the library kind and censor tags nothing reads (build 252, item 21).

A library once carried "suggest this library for" tags: calendar kinds (TV, MOVIE, OVA ...) and a censoring
value. The interface for both went in build 201; the columns stayed, unread. Adult and the season hint remain.

Revision ID: 20261013_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_01"
down_revision = "20261012_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch:
        batch.drop_column("fits_kinds")
        batch.drop_column("fits_censor")


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch:
        batch.add_column(sa.Column("fits_kinds", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("fits_censor", sa.String(length=12), nullable=True))

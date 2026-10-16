"""The season a show belongs to, on the anime (build 190, 0.8.2).

Two nullable columns: ``season`` (WINTER | SPRING | SUMMER | FALL) and
``season_year``. Additive only - nothing existing is touched, and nothing is
filled here: a migration cannot read the calendar store. The worker fills them
from the calendar (services/anime_season.py), and until it has, the watchlist
works the season out from the premiere date as before.

Revision ID: 20261002_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_01"
down_revision = "20260929_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.add_column(sa.Column("season", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("season_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.drop_column("season_year")
        batch.drop_column("season")

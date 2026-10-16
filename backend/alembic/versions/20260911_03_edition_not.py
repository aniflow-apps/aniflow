"""Profiles can refuse an edition as well as require one.

The copies of one episode are alternatives, so a row that is *not* the
dual-audio one has to refuse the dual-audio file - which carries everything
the plain row asks for and one thing more. Requiring alone let it through, and
picking the plain row listed two files for every episode.

Empty is "refuse nothing", which is what every profile written before this
meant.

Revision ID: 20260911_03
Revises: 20260911_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_03"
down_revision = "20260911_02"
branch_labels = None
depends_on = None

_TABLES = ("release_profiles", "profile_templates")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "edition_not",
                    sa.JSON(),
                    nullable=False,
                    # Server-side: existing rows need a value, and the model
                    # default only applies to rows Python creates.
                    server_default=sa.text("'[]'"),
                )
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("edition_not")

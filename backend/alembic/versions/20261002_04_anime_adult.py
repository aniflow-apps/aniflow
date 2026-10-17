"""An adult flag on the anime (build 192, 0.8.5).

One boolean, false for everything that exists. The user sets it, and a new
anime starts from what its source said.

Revision ID: 20261002_04
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_04"
down_revision = "20261002_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.add_column(
            sa.Column("is_adult", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("anime") as batch:
        batch.drop_column("is_adult")

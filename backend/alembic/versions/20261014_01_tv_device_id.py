"""A television's own id on its token (build 263).

Re-pairing a TV made a second entry for it: a token is stored only as a hash, and a pairing carried only the TV's
name, so nothing could say "this is the TV that already has an entry". The TV now sends an id of its own when it
asks to be paired, and the entry it already has is replaced when it collects the new token. Nullable: every token
made before this has none, and a TV that cannot make one still pairs as it always did.

Revision ID: 20261014_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_01"
down_revision = "20261013_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tv_tokens") as batch:
        batch.add_column(sa.Column("device_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_tv_tokens_device_id", ["device_id"])


def downgrade() -> None:
    with op.batch_alter_table("tv_tokens") as batch:
        batch.drop_index("ix_tv_tokens_device_id")
        batch.drop_column("device_id")

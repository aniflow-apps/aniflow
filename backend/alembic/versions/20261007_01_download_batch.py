"""Two columns for taking single episodes out of a batch torrent (build 213, 0.8.6).

A batch is one torrent that serves several episodes. The first wanted episode keeps the torrent as it
always did (its ``info_hash`` is unchanged, and still unique); each further wanted episode gets a row of its
own with no hash, pointing at the first through ``batch_download_id`` and naming its file in ``batch_file``.

Both are null for every ordinary row, so nothing that exists changes. See docs/batch-episodes-plan.md.

Revision ID: 20261007_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_01"
down_revision = "20261002_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.add_column(sa.Column("batch_download_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("batch_file", sa.String(length=500), nullable=True))
        batch.create_index("ix_downloads_batch_download_id", ["batch_download_id"])


def downgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.drop_index("ix_downloads_batch_download_id")
        batch.drop_column("batch_file")
        batch.drop_column("batch_download_id")

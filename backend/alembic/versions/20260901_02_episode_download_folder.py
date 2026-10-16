"""episode download folder

Stores the torrent client's category subfolder per episode, so an episode's
source path is fully derivable from stored data:

    download_root / download_folder / download_filename

Previously the importer assumed qBittorrent's ``<download_root>/<category>/``
mapped onto AniFlow's ``<download_root>/`` and stripped the category
unconditionally. When both containers mount the folder on the same path (the
common case) that produced a path which does not exist, and the import failed
with ``source_missing``.

Additive and nullable: existing rows keep working, and NULL means "directly in
the download root".

Revision ID: 20260901_02
Revises: 20260901_01
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_02"
down_revision = "20260901_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episodes", sa.Column("download_folder", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("episodes", "download_folder")

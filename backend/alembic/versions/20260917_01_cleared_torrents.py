"""Torrents the user deliberately forgot (build 128).

Reported as *"Clear all in the downloads page, does only clear it temp the
stuff in there comes back. Why?"*

Because two features promise opposite things about the same torrent, and both
were working as designed:

* **Clear** forgets AniFlow's download row and leaves the torrent in the
  client and the file on disk. That is its whole contract.
* **Orphan adoption** (build 119) walks the client for finished torrents in
  AniFlow's category that no row knows about, and adopts them. Its docstring
  even names this case: *"A cleared Downloads list, a swap that forgot them, a
  database restored from before the download."*  It was written to rescue five
  episodes whose rows had gone while their files sat finished in the client.

So after a Clear, every finished torrent in the category satisfies the orphan
rule exactly, and the half-hourly pass adopts them all back. The list refills
itself.

This table is the missing piece: the hashes somebody cleared on purpose. The
background pass skips them; an explicit *Fix missing episodes* does not,
because that is the user asking for adoption in so many words.

The info hash rather than the download id: the row is gone, the torrent is
what remains, and the hash is the only thing both sides agree on.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260917_01"
down_revision = "20260916_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cleared_torrents",
        sa.Column("info_hash", sa.String(length=40), primary_key=True),
        sa.Column("label", sa.String(length=500), nullable=True),
        sa.Column(
            "cleared_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("cleared_torrents")

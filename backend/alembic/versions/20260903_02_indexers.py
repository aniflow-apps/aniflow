"""Indexers become user-configured, and the stored provider name loses its brand.

AniFlow shipped one search provider with a site baked in, named in the code, in
the settings defaults and in the ``provider`` column of every discovered
release. A public build ships none: the user adds an indexer with a name and a
URL. Existing rows keep working - the provider value is rewritten to the
neutral one the code now uses.

Revision ID: 20260903_02
"""
import sqlalchemy as sa
from alembic import op

revision = "20260903_02"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indexers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=250), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_indexer_name"),
    )

    # Releases discovered through the old built-in provider were stored under
    # its site name. The column says *how* a release was found, so the value
    # becomes the neutral "indexer" - nothing about the row changes otherwise.
    releases = sa.table(
        "episode_releases",
        sa.column("provider", sa.String),
    )
    op.execute(
        releases.update().where(releases.c.provider == "nyaa").values(provider="indexer")
    )


def downgrade() -> None:
    releases = sa.table("episode_releases", sa.column("provider", sa.String))
    op.execute(
        releases.update().where(releases.c.provider == "indexer").values(provider="nyaa")
    )
    op.drop_table("indexers")

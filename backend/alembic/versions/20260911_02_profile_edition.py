"""Profiles can pin an edition.

Which *copy* of an episode a profile wants: ["uncensored", "dual-audio"], the
canonical markers services/release_edition.py reads out of a release name.

One group publishes three files per episode - a censored Amazon rip, an
uncensored one, and an uncensored one with a second audio track - and until
build 97 nothing could tell the last two apart, let alone remember which one
you chose. Censorship and the media source had columns already; a column per
subtitle set and streaming platform would be a migration per fansub fashion,
so this is a list.

Empty is "any edition", which is what every profile written before this
meant - so existing rows keep behaving exactly as they did.

Revision ID: 20260911_02
Revises: 20260911_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_02"
down_revision = "20260911_01"
branch_labels = None
depends_on = None

#: Both tables carry the same rules: a template is the reusable form of a
#: profile and applying one copies its values across.
_TABLES = ("release_profiles", "profile_templates")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "edition",
                    sa.JSON(),
                    nullable=False,
                    # Server-side, because existing rows need a value and the
                    # model's default only applies to rows Python creates.
                    server_default=sa.text("'[]'"),
                )
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("edition")

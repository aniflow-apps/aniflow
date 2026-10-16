"""Reclassify EPISODE_NOT_IDENTIFIABLE out of AMBIGUOUS_RELEASE (168).

Reported live: two shows, both fully in the library, both showing "Several
matches" with an "Assign to anime" button that did nothing useful. Every
AMBIGUOUS_RELEASE attention with ``anime_id`` set was never actually about
which anime - services/downloads.py set ``anime_id`` straight from the
release two lines above the call, so there was nothing to pick between. It
was really about the episode number: a MATCHED release whose name carried
none, almost always a batch. Nothing else has ever raised AMBIGUOUS_RELEASE
with ``anime_id`` set - a genuinely ambiguous release keeps it null
(services/rss_scan.py, decision=AMBIGUOUS) - so every row this WHERE matches
is one of these, with no false positives to worry about.

Without this, an installation upgrading keeps the old, wrong row forever: the
next queue pass raises a fresh, correctly-kinded record for the same release
rather than touching the old one, because raise_attention dedupes on
``(kind, subject)`` and the kind just changed - so the two would sit side by
side instead of one replacing the other.

Also widens ``attention.kind``. The column's declared width has always
tracked the longest member of ``AttentionKind`` at the time it was migrated -
17 characters, from the original five - and every kind added since (166)
happened to fit under that by luck rather than by a migration anyone wrote.
``EPISODE_NOT_IDENTIFIABLE`` is 24 and is the first not to; SQLite itself
never enforced the old width, but test_migrations_match_models.py compares
the *declared* width against it, and a real deployment on a database that
does enforce it would have failed outright.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260925_02"
down_revision = "20260925_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attention") as batch:
        batch.alter_column("kind", existing_type=sa.String(length=17), type_=sa.String(length=24))
    op.execute(
        "UPDATE attention SET kind = 'EPISODE_NOT_IDENTIFIABLE' "
        "WHERE kind = 'AMBIGUOUS_RELEASE' AND anime_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE attention SET kind = 'AMBIGUOUS_RELEASE' "
        "WHERE kind = 'EPISODE_NOT_IDENTIFIABLE'"
    )
    with op.batch_alter_table("attention") as batch:
        batch.alter_column("kind", existing_type=sa.String(length=24), type_=sa.String(length=17))

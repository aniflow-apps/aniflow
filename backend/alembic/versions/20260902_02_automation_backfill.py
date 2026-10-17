"""Switch Automate on where automation was already happening.

Until now the RSS path queued every matched release regardless of the per-anime
Automate switch, so a tracked anime downloaded on its own with the switch
showing off. The switch is the real gate from 0.0.59 on; this backfill puts it
in the position that matches what the installation was already doing, so an
upgrade changes nothing until the user flips a switch themselves.

Only the lifecycles that were being auto-queued are touched (active and
upcoming) - dropped, archived and complete anime were always skipped.

Revision ID: 20260902_02
"""
import sqlalchemy as sa
from alembic import op

revision = "20260902_02"
down_revision = "20260902_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    anime = sa.table(
        "anime",
        sa.column("automation_enabled", sa.Boolean),
        sa.column("lifecycle", sa.String),
    )
    op.execute(
        anime.update()
        .where(anime.c.lifecycle.in_(("ACTIVE", "UPCOMING")))
        .values(automation_enabled=True)
    )


def downgrade() -> None:
    # The switch's previous position is not recoverable, and off is the value
    # the column was introduced with.
    anime = sa.table("anime", sa.column("automation_enabled", sa.Boolean))
    op.execute(anime.update().values(automation_enabled=False))

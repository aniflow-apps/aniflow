"""Two kinds of leftover file, told apart (build 119).

``superseded_files`` was built for one job: a file kept until its replacement
arrives, then deleted automatically. A swap in ``backup`` mode leaves a
different kind of leftover - a file renamed to ``.backup`` and left for the
user - and the difference matters in exactly one place: nothing may delete
those by itself.

Asked for as part of the removal lifecycle: *"if you have the settings to
rename, it needs to rename them (and it needs to have the renamed tracked in
the database, maybe the user later wants to remove them)"*.

Every existing row is the first kind, which is what the default says.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_03"
down_revision = "20260915_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "superseded_files",
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
            server_default="awaiting_replacement",
        ),
    )


def downgrade() -> None:
    op.drop_column("superseded_files", "kind")

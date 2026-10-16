"""Persist each schedule task's last run, so a redeploy does not lose it (161).

Reported: *"I noticed when I put a new build version up it always resets the
times, so in theory a 1 week update will never happen, because we constantly
updating aniflow currently."* Correct - `WorkerState`/`TaskRuntimeState` only
ever lived in the process's own memory, so every redeploy started every
task's clock over from "just booted", and an install redeployed often enough
never actually let a slow interval elapse. This table is the fix: one row per
task id, read at worker startup and written after every run.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260924_01"
down_revision = "20260917_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_task_state",
        sa.Column("task_id", sa.String(length=80), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.String(length=400), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("schedule_task_state")

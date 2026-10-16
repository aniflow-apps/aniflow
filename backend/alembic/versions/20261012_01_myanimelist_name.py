"""One name for MyAnimeList in anime.provider_ids (item 53).

No schema change: the JSON column's ``mal`` key is renamed ``myanimelist``,
which is what the adapters have always written. Where both are present the
existing ``myanimelist`` value stays and ``mal`` is dropped. The model now
renames on every write, so this only has to catch what is already stored.

Revision ID: 20261012_01
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "20261012_01"
down_revision = "20261008_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, provider_ids FROM anime")).fetchall()
    for anime_id, raw in rows:
        ids = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(ids, dict) or "mal" not in ids:
            continue
        fixed = {key: value for key, value in ids.items() if key != "mal"}
        if not fixed.get("myanimelist"):
            fixed["myanimelist"] = ids["mal"]
        bind.execute(
            sa.text("UPDATE anime SET provider_ids = :ids WHERE id = :id"),
            {"ids": json.dumps(fixed), "id": anime_id},
        )


def downgrade() -> None:
    """Nothing to undo: both spellings are read everywhere."""

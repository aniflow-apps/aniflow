"""API schemas for torrent downloads."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DownloadResponse(BaseModel):
    id: str
    release_id: str | None
    anime_id: str | None
    episode_id: str
    anime_name: str | None = None
    release_name: str | None = None
    label: str | None = None
    info_hash: str | None
    # Read from the episode relationship (TorrentDownload.episode_number) but
    # serialised as "episode"; the attribute itself is the ORM relationship.
    episode: int | None = Field(default=None, validation_alias="episode_number")
    version: int
    status: str
    progress: float
    qb_state: str | None
    attention_reason: str | None
    error: str | None
    imported: bool = False
    #: How many other torrents there are for this same episode and version
    #: (build 88). Reported rather than hidden: two groups' copies of one
    #: episode is a real thing that happened, and the reader should be able to
    #: see that it did.
    duplicates: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QueueTriggerResponse(BaseModel):
    started: bool


class DownloadsStatusResponse(BaseModel):
    configured: bool
    category: str
    # Which pipeline is running, so the page can say where files go.
    mode: str
    queue_pending: bool
    last_queue_summary: dict[str, object] | None
    last_sync_summary: dict[str, object] | None
    last_import_summary: dict[str, object] | None
    last_error: str | None
    # What the worker will do on its own, so the page can say so plainly.
    monitor_interval_seconds: int = 30
    after_import_action: str = "pause"
    worker_running: bool = False


class MediaFileResponse(BaseModel):
    """One episode's placed media file. ``id`` is the episode id."""

    id: str
    anime_id: str | None
    anime_name: str | None = None
    episode: int
    relative_path: str
    size_bytes: int | None = None
    version: int = 1
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

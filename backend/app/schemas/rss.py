"""API schemas for RSS sources, scans and discovered releases."""
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class FeedSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: AnyHttpUrl
    is_default: bool = False


class FeedSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: AnyHttpUrl | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class FeedSourceResponse(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    # Derived from the release history, not stored on the source.
    #: Set when an indexer owns this source. Such a source can be made the
    #: default but not edited or deleted here - it belongs to the indexer.
    indexer_id: str | None = None
    indexer_name: str | None = None
    release_count: int = 0
    #: Of those, the ones that reached a watchlist anime. A feed with
    #: discoveries but no matches is configured but useless.
    matched_count: int = 0
    last_release_at: datetime | None = None
    last_match_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ReleaseResponse(BaseModel):
    id: str
    raw_name: str
    torrent_url: str | None
    published_at: datetime | None
    parsed_title: str | None
    subgroup: str | None
    episode: str | None
    version: int
    resolution: str | None
    media_source: str | None
    decision: str
    anime_id: str | None
    anime_name: str | None = None
    source_name: str | None = None
    failure_reasons: list[str]
    ambiguous_anime_ids: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RssStatusResponse(BaseModel):
    worker_running: bool
    scanning: bool
    scan_requested: bool
    last_scan_at: datetime | None
    next_scan_at: datetime | None
    last_scan_error: str | None
    last_scan_summary: dict[str, object] | None
    interval_minutes: int
    # Whether scheduled scanning is switched on at all; a stopped schedule
    # is not the same as a worker that is down.
    enabled: bool = True


class ReleasesClearedResponse(BaseModel):
    cleared: int


class ScanTriggerResponse(BaseModel):
    started: bool

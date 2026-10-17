"""API schemas for the Attention workflow and Activity log."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttentionResponse(BaseModel):
    id: str
    kind: str
    status: str
    anime_id: str | None
    release_id: str | None
    download_id: str | None
    feed_source_id: str | None
    anime_name: str | None = None
    feed_name: str | None = None
    #: Which episode, when the record is about a download. "Saga of Tanya the
    #: Evil II" three times over said nothing about which three episodes, and
    #: the row is meant to be actionable.
    episode_number: int | None = None
    #: The release that was queued, which is the name qBittorrent knows it by
    #: - or, before there is a download to ask, the release's own raw name
    #: (build 168), so a row about a release is never silent about which one.
    release_label: str | None = None
    #: The episode as AniFlow read it off that name - "01-12" for a batch,
    #: say - alongside release_label so a person can see *why* a number could
    #: not be picked, not just that one could not (build 168).
    episode_label: str | None = None
    #: Which resolutions this record offers, in the order they should read.
    #: Decided on the server: whether a duplicate can be resolved by deleting
    #: the download depends on the file still being there, which the browser
    #: cannot know. See services/attention.actions_for.
    actions: list[str] = ["resolve", "dismiss"]
    #: For a duplicate: the file that was downloaded, and the one already in
    #: the library that is blocking it. Names, not paths - enough to tell them
    #: apart, which is the decision being asked for.
    download_file: str | None = None
    library_file: str | None = None
    #: How many further warnings this row stands for (build 89). Two download
    #: rows for one episode raise one warning each, and they are about the
    #: same file: the row is shown once and says so.
    duplicates: int = 0
    detail: str | None
    note: str | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AttentionResolutionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class AssignAnimeRequest(BaseModel):
    anime_id: str = Field(min_length=1)


class ActivityClearResponse(BaseModel):
    """How many diary entries were discarded."""

    deleted: int


class ActivityResponse(BaseModel):
    id: str
    kind: str
    # routine | notable | problem - derived from the kind and its counters.
    level: str = "notable"
    message: str
    details: dict[str, object]
    anime_id: str | None
    anime_name: str | None = None
    #: Which episode, when the entry is about one (build 164) - what lets a
    #: history view tell "episode 84" apart from "episode 85" of the same show.
    episode_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

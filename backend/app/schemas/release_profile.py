"""API schemas for release profiles and deterministic matching."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated

from backend.app.models.release_profile import CensorPreference

# Canonical values shared with the release-name parser.
ALLOWED_RESOLUTIONS = ("4320p", "2160p", "1440p", "1080p", "720p", "540p", "480p")
ALLOWED_MEDIA_SOURCES = ("web_dl", "webrip", "web", "remux", "bluray", "hdtv", "dvd", "tv", "vhs")

_SubgroupEntry = Annotated[str, StringConstraints(min_length=1, max_length=80)]
_Resolution = Annotated[str, StringConstraints(pattern="^(4320p|2160p|1440p|1080p|720p|540p|480p)$")]
_MediaSource = Annotated[str, StringConstraints(pattern="^(web_dl|webrip|web|remux|bluray|hdtv|dvd|tv|vhs)$")]


class ReleaseProfileUpsert(BaseModel):
    subgroups: list[_SubgroupEntry] = Field(default_factory=list, max_length=12)
    resolution: _Resolution | None = None
    media_source: _MediaSource | None = None
    censor_preference: CensorPreference = CensorPreference.ANY
    require_title_match: bool = True
    require_resolution_known: bool = False
    require_source_known: bool = False
    require_censor_known: bool = False


class ReleaseProfileResponse(ReleaseProfileUpsert):
    id: str
    anime_id: str
    template_id: str | None = None
    template_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestReleaseRequest(BaseModel):
    release_name: str = Field(min_length=1, max_length=500)


class ParsedReleaseResponse(BaseModel):
    raw_name: str
    subgroup: str | None
    title: str | None
    episode: str | None
    version: int
    resolution: str | None
    media_source: str | None
    video_codec: str | None
    audio_codec: str | None
    censored: bool | None
    unknown_tags: list[str]


class FieldVerdictResponse(BaseModel):
    field: str
    verdict: str
    expected: str | None = None
    actual: str | None = None


class ProfileTestResponse(BaseModel):
    parsed: ParsedReleaseResponse
    matched: bool
    fields: list[FieldVerdictResponse]
    failures: list[str]

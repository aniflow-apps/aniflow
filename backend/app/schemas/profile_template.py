"""API schemas for reusable profile templates."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated

_SubgroupEntry = Annotated[str, StringConstraints(min_length=1, max_length=80)]
_Resolution = Annotated[str, StringConstraints(pattern="^(4320p|2160p|1440p|1080p|720p|540p|480p)$")]
_MediaSource = Annotated[str, StringConstraints(pattern="^(web_dl|webrip|web|remux|bluray|hdtv|dvd|tv|vhs)$")]


class ProfileTemplateUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    subgroups: list[_SubgroupEntry] = Field(default_factory=list, max_length=12)
    resolution: _Resolution | None = None
    media_source: _MediaSource | None = None
    censor_preference: str = Field(default="ANY", pattern="^(ANY|UNCENSORED|CENSORED)$")
    require_title_match: bool = True
    require_resolution_known: bool = False
    require_source_known: bool = False
    require_censor_known: bool = False
    is_default: bool = False


class ProfileTemplateResponse(ProfileTemplateUpsert):
    id: str
    is_default: bool
    anime_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplyTemplateRequest(BaseModel):
    """Null template_id detaches the anime from any template (values kept)."""

    template_id: str | None = None


class ApplyTemplateResponse(BaseModel):
    applied: bool
    template_name: str | None = None


class FeedReadResult(BaseModel):
    """One feed the test read, and what came of reading it."""

    name: str
    items: int = 0
    error: str | None = None


class GroupSightingResult(BaseModel):
    """One subgroup a profile names, and whether the feeds carry it.

    ``items`` at zero while the feeds hold hundreds is the finding this whole
    endpoint exists for: the group name is wrong.
    """

    name: str
    items: int = 0
    resolutions: list[str] = []
    example: str | None = None


class ProfileTestResultResponse(BaseModel):
    template_id: str
    name: str
    #: ok | no-group | no-match - decided on the server so both surfaces
    #: agree on what the numbers mean.
    verdict: str
    accepted: int = 0
    considered: int = 0
    groups: list[GroupSightingResult] = []
    examples: list[str] = []
    #: The rules that threw out the most, worst first: (rule, count).
    top_failures: list[tuple[str, int]] = []


class ProfileTestResponse(BaseModel):
    feeds: list[FeedReadResult] = []
    results: list[ProfileTestResultResponse] = []
    #: How many releases the feeds carried in total.
    items: int = 0
    #: False when not one feed could be read. Then every count is zero for a
    #: reason that has nothing to do with the profiles.
    reached_any_feed: bool = False

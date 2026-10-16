"""API schemas for the In Progress page and its Archive (build 164-165)."""
from datetime import datetime

from pydantic import BaseModel


class ProgressHistoryEntry(BaseModel):
    #: Both are one of services/episode_view.py:STATES, or null for
    #: ``from_state`` on the very first step an episode ever had.
    from_state: str | None
    to_state: str
    created_at: datetime


class ProgressItemResponse(BaseModel):
    episode_id: str
    anime_id: str
    anime_name: str
    episode_number: int
    #: One of services/episode_view.py:STATES - the same vocabulary the
    #: watchlist reads, so a state means the same thing on both pages.
    state: str
    detail: str | None
    progress: float | None
    #: When the episode entered this state, for "how long has this been like
    #: this" - None when nothing says.
    since: datetime | None
    #: When this episode was archived, for the Archive page. Always null on
    #: the main listing.
    archived_at: datetime | None = None
    #: The release AniFlow queued - three rows naming only the anime are
    #: indistinguishable once more than one thing is happening to it.
    release_label: str | None = None
    #: Which buttons this card offers - the same ids and the same server-side
    #: decision Attention rows use (services/attention.py:actions_for). Acted
    #: on through the existing `/api/activity/attention/{attention_id}/...`
    #: endpoints, which is what ``attention_id`` is for - this page raises no
    #: actions of its own, it only surfaces the ones Attention already has.
    actions: list[str] = []
    attention_id: str | None = None
    #: The attention's own kind (models.activity.AttentionKind), so the
    #: frontend can reuse attentionRemedy.ts's existing "this fixes itself"
    #: wording instead of a second copy of it.
    attention_kind: str | None = None
    #: The two files a duplicate is about (build 167) - the same pair the
    #: Attention page named, so hiding a duplicate's row there once its
    #: episode is on this page (see routes/activity.py) does not also hide
    #: which file is which before Delete download / Replace library is
    #: pressed. Null whenever there is nothing to delete or replace.
    download_file: str | None = None
    library_file: str | None = None
    #: For a duplicate-files case (build 189): the older copies sitting next to
    #: ``library_file``, by name.
    older_files: list[str] = []
    history: list[ProgressHistoryEntry] = []


class ProgressDismissResponse(BaseModel):
    dismissed: bool = True

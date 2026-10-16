"""What one episode says about itself to a person (build 125).

The complaint this answers: *"AniFlow is doing stuff under the hood and its
not obvious for the user, its not even obvious for me as maker."*  Part of
that was genuinely invisible work, and part was this - the interface had **two
vocabularies for one question** and switched between them per row.

The two:

* ``EpisodeStatus`` - nine values, derived from the episode's own facts by
  services/episode_state.py and written to the row.
* ``DownloadStatus`` - seven values, written from what qBittorrent reports.

Both answer "where is this episode", and the detail row picked whichever was
more specific for that row. So the same episode could read "Owned" on one line
and "In library" on the next, and a downloading one read "Downloading" from
one vocabulary while the row beneath it read "Added to torrent" from the
other. Nobody can hold that in their head - and the row was also stating its
status twice in two of those words at once.

This module is the one place that decides what a person reads. It is
**derived on read and never stored**: there is already a materialised
derivation (``episode.status``), and a second stored opinion is how the first
problem happened. Nothing here renames an enum or changes a stored value; the
internal distinctions all survive, because several of them are load-bearing
(services/downloads.py needs to know FAILED from SKIPPED even though a person
does not).

Three rules it follows
----------------------

**One state, plus a detail.** The state says *what*; the detail says
*where or why*. So a failed import is ``failed`` + ``target_exists`` rather
than its own state - the twelve reason codes an import can fail with are
genuinely different problems with different fixes, but they are all "this did
not work" to somebody reading a list.

**Grey is not red.** An entry nobody is fetching for - a tracker entry, a show
whose season has not started, one the user asked to be left alone - is
``not_tracked`` or ``not_aired``, never ``failed``. Build 118 had to undo
exactly this mistake once already, when every Shoko half of every card went
red for a situation that was nobody's fault.

**No release is not an error.** ``no_release_yet`` covers both "nobody has
published this" and "somebody published it and it is not the group you asked
for". Those are worth telling apart and currently cannot be: only *matching*
releases are attached to an episode (services/rss_scan.py), so the data to
split them does not exist yet. One honest coarse state now, split when it
does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.models.anime import Episode, EpisodeStatus, EpisodeType
from backend.app.models.download import DownloadStatus

#: Every state a person can be shown, in rough lifecycle order.
#:
#: Written down because the interface translates them - a state missing from
#: the locale files prints its own code at the user, and a test walks this
#: tuple against both files. The same reason QB_STATES and reasons.py are
#: written down.
STATES = (
    # Nothing is wrong and nothing is expected yet.
    "not_aired",
    "no_release_yet",
    "not_tracked",
    # The user's AniDB list says they have it (item 36b): not fetched, and not a gap.
    "skipped",
    # Something is expected.
    "ready",
    "downloading",
    # Still DOWNLOADING underneath (episode_state.derive_status keeps it
    # there on purpose - the queue pass or the stall clock is what fixes
    # this, not a person), but "downloading" alone read as progress that
    # had simply gone quiet rather than a torrent nobody is chasing any
    # more.
    "download_missing",
    "download_stalled",
    "moving_to_library",
    "moving_to_shoko",
    "with_shoko",
    # Done, or needing a decision.
    "complete",
    "new_version",
    # Two files for one episode (build 189): an original kept by a swap, or a
    # v1 next to a v2. Nothing is wrong, but somebody has to choose.
    "duplicate_files",
    "file_gone",
    "failed",
)

#: The states a person can act on from the episode row. The row offers a
#: button for these and says nothing for the rest, which is what keeps a
#: healthy list quiet.
ACTIONABLE = frozenset({"ready", "file_gone", "new_version", "duplicate_files", "failed"})


@dataclass(frozen=True)
class EpisodeView:
    """One episode, as a person reads it."""

    #: One of :data:`STATES`.
    state: str
    #: Why or where, when there is more to say: one of the reason codes from
    #: services/reasons.py, a raw qBittorrent state, or None. The interface
    #: already knows how to put words to both (frontend/src/reasons.ts).
    detail: str | None = None
    #: 0.0-1.0 while the client owns it, else None.
    progress: float | None = None

    @property
    def actionable(self) -> bool:
        return self.state in ACTIONABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "detail": self.detail,
            "progress": self.progress,
            "actionable": self.actionable,
        }


def _aired(episode: Episode, *, now: datetime | None = None) -> bool:
    """Whether this episode exists yet.

    No date means aired: plenty of finished shows have no per-episode dates,
    and treating those as upcoming would hide the whole back catalogue behind
    a state that says "nothing is wrong".
    """
    when = episode.aired_at
    if when is None:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when <= (now or datetime.now(UTC))


def view_for(
    episode: Episode,
    *,
    anime=None,
    shoko_mode: bool = False,
    now: datetime | None = None,
) -> EpisodeView:
    """What this episode says about itself.

    Pure: no session, no filesystem, no network. Everything comes from the
    episode, its download row and its anime - all of which the caller already
    has loaded, because the serializer is walking them anyway.

    ``anime`` defaults to ``episode.anime``; it is a parameter so this can be
    tested without a relationship and so a caller with the anime in hand does
    not trigger a lazy load per episode.
    """
    owner = anime if anime is not None else episode.anime
    download = episode.download
    status = episode.status

    # --- the cases that are nobody's fault, first ---------------------------
    #
    # Checked ahead of everything else on purpose. An entry nobody is fetching
    # for must not be able to report a fault, and an episode that has not
    # happened yet must not read as a gap. Both outrank the pipeline, because
    # both are statements about *whether the pipeline applies at all*.
    #
    # Not while something is in flight (item 60): a torrent already running, or
    # one the user started with a button, is a fact the row has to show. Reading
    # "Not being looked for" under a progress bar said the opposite of what the
    # client was doing.
    in_flight = status in (EpisodeStatus.DOWNLOADING, EpisodeStatus.IMPORTING, EpisodeStatus.DOWNLOADED)
    if status is not EpisodeStatus.COMPLETE and not episode.media_filename and not in_flight:
        if owner is not None and getattr(owner, "releases_ignored", False):
            return EpisodeView("not_tracked", detail="releases_ignored")
        if owner is not None and getattr(owner, "entry_mode", None) == "basic":
            return EpisodeView("not_tracked", detail="tracker_entry")
        if owner is not None and getattr(owner, "entry_mode", None) == "future":
            return EpisodeView("not_aired", detail="not_aired_yet")

    if (
        getattr(episode, "skip_reason", None)
        and status is not EpisodeStatus.COMPLETE
        and not episode.media_filename
        and not in_flight
    ):
        return EpisodeView("skipped", detail=episode.skip_reason)

    # --- in flight ----------------------------------------------------------
    if status is EpisodeStatus.DOWNLOADING:
        if download is not None and download.attention_reason == "missing_in_client":
            # derive_status keeps this DOWNLOADING on purpose - the queue
            # pass re-hands a missing torrent on its own - but "downloading"
            # plus a stale client word read as ordinary progress that had
            # simply gone quiet, not a torrent nobody is chasing any more.
            return EpisodeView("download_missing", progress=download.progress)
        if download is not None and _stalled_long_enough(download, now=now):
            return EpisodeView("download_stalled", progress=download.progress)
        return EpisodeView(
            "downloading",
            # The client's own word for what it is doing. More specific than
            # anything this side could say, and the interface already
            # translates it.
            detail=download.qb_state if download is not None else None,
            progress=download.progress if download is not None else None,
        )

    if status is EpisodeStatus.IMPORTING:
        # Shoko has the file and has not filed it yet. A legitimate wait, not
        # an error - Shoko has its own queue and AniFlow does not know the
        # final folder until it appears. The two ways this wait goes wrong
        # (shoko_not_imported, shoko_file_gone) arrive as a download
        # attention_reason and are reported as `failed` below, because by then
        # it is no longer a wait.
        blocker = download.attention_reason if download is not None else None
        if blocker in ("shoko_not_imported", "shoko_file_gone"):
            return EpisodeView("failed", detail=blocker)
        return EpisodeView("with_shoko")

    if status is EpisodeStatus.DOWNLOADED:
        # The download itself finished, which is worth saying: "moving to
        # library" alone left people unsure whether the fetch had worked.
        return EpisodeView(
            "moving_to_shoko" if shoko_mode else "moving_to_library",
            detail=download.attention_reason if download is not None else None,
        )

    # --- finished, or needing a decision ------------------------------------
    if status is EpisodeStatus.COMPLETE:
        return EpisodeView("complete")

    if status is EpisodeStatus.UPDATE:
        # A newer version exists for a file already owned. Never acted on
        # automatically - see services/matching.decide_version.
        return EpisodeView("new_version")

    if status is EpisodeStatus.MISSING:
        # Acquired once and the file has gone. Distinct from never having had
        # it, because the release it came from is usually still known, so this
        # one can be offered a re-download.
        return EpisodeView("file_gone")

    # --- gone wrong ---------------------------------------------------------
    if status is EpisodeStatus.ERROR:
        return EpisodeView("failed", detail=_failure_detail(episode, download))

    if download is not None and download.status is DownloadStatus.FAILED:
        # Never handed to the client at all. `derive_status` normally turns
        # this into ERROR; caught here as well so a row mid-refresh cannot
        # read as "ready" while its download is a failure.
        return EpisodeView("failed", detail=_failure_detail(episode, download))

    # --- waiting -----------------------------------------------------------
    if status is EpisodeStatus.AVAILABLE:
        # `AVAILABLE` means at least one *matching* release is attached, and
        # only matching ones ever are - so there is something fetchable here,
        # which is what makes it honest to offer a button.
        return EpisodeView("ready")

    if not _aired(episode, now=now):
        return EpisodeView("not_aired")
    if _left_out_of_anidb_list(episode, owner):
        # Two things are true of this episode, and only one used to be said (build 266): it has no
        # release, and it is the one the AniDB list does not have while its neighbours are in it.
        return EpisodeView("no_release_yet", detail=NOT_IN_ANIDB_LIST)
    return EpisodeView("no_release_yet")


#: The detail of a ``no_release_yet`` episode that the user's AniDB list lacks (build 266).
NOT_IN_ANIDB_LIST = "not_in_anidb_list"


def _left_out_of_anidb_list(episode: Episode, owner) -> bool:
    """Whether the anime came from the AniDB list, with this episode the one that is not in it."""
    if owner is None or episode.skip_reason or episode.episode_type is not EpisodeType.REGULAR:
        return False
    return any(other.skip_reason for other in owner.episodes)


def _stalled_long_enough(download, *, now: datetime | None = None) -> bool:
    """Whether this download has been stuck long enough to say so.

    Mirrors ``services/downloads.py::stalled_downloads`` - the same
    ``STALLED_AFTER`` threshold, imported rather than repeated, so the two
    can never quietly disagree on how long is long enough. Every torrent
    stalls briefly while it looks for peers; only the clock, not the raw
    ``stalled_since`` timestamp, tells the two apart.
    """
    if download.stalled_since is None:
        return False
    from backend.app.services.downloads import STALLED_AFTER

    since = download.stalled_since
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - since >= STALLED_AFTER


def _failure_detail(episode: Episode, download) -> str | None:
    """The most specific thing known about why this did not work.

    Order matters: the download's own reason is a code with a translation and
    a known fix, so it beats ``episode.error``, which is free text from
    whichever service gave up - useful, but not something the interface can
    put proper words to.
    """
    if download is not None and download.attention_reason:
        return download.attention_reason
    if episode.error:
        return episode.error
    if download is not None and download.error:
        return download.error
    return None


__all__ = ["ACTIONABLE", "STATES", "EpisodeView", "view_for"]

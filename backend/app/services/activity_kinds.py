"""Every kind of thing the Activity log records (build 125).

Written down for the reason services/reasons.py and QB_STATES are written
down: the interface translates these, and a kind with no translation prints
its own identifier at the user. They were free strings until this build -
nineteen of them in use, no registry - so a typo silently became a twentieth
kind at default importance and nobody could enumerate what the log could say
without grepping the services.

What belongs here, and what does not
------------------------------------

The rule the rework settled on: *"One successful acquisition should normally
result in one meaningful Activity event, not dozens of technical events."*

So an entry is worth making when it names **something that happened to an
anime or an episode**. It is not worth making for a *pass* that ran.
``SCAN_COMPLETED``, ``SYNC_PASSED`` and ``QUEUE_PASSED`` were per-pass and are
gone: at a sixty-minute scan interval the first alone wrote about 8,760 rows a
year, none of which said anything, and all three were **already on screen**
from the worker's in-memory state - ``last_scan_summary`` on the RSS status
box, ``last_sync_summary`` and ``last_import_summary`` on the Downloads one.
Logging them to Activity copied transient information into permanent history.

The result is a log that is both smaller and more useful: a twelve-episode
season now writes twelve rows that each name an episode, instead of three that
name a pass.
"""

#: What happened to an anime or an episode. Per-episode where the event is,
#: and the reason each one exists is written beside it.
EPISODE_KINDS = (
    # An episode reached the library. The one entry a successful acquisition
    # makes (build 125), replacing the per-pass IMPORT_PASSED.
    "EPISODE_ACQUIRED",
    # An episode that finished downloading and could not be placed. One entry
    # per new or changed reason, never per pass - the import pass runs every
    # thirty seconds and a retried blocker used to write the same line for
    # ever. The Attention record beside it says it is blocked *now*; this is
    # what remains in the history once it is resolved.
    "EPISODE_BLOCKED",
    # A feed carried an episode of something tracked. Already per-episode.
    "RSS_EPISODE_FOUND",
    # A file that was already in the library, adopted rather than imported.
    "LIBRARY_ADOPTED",
    # A file in the library that no episode claims.
    "LIBRARY_UNPLACED",
    # The old file after a version swap: deleted, or left behind.
    "LIBRARY_SUPERSEDED",
    "LIBRARY_SUPERSEDED_LEFT",
    # One of two files for an episode, deleted by the user's choice.
    "LIBRARY_DUPLICATE_DELETED",
    # Files renamed by a release change.
    "FILES_RENAMED",
    # The lookup that attaches an anime's releases to its episodes, right after adding or
    # converting it, did not finish (build 231). It used to leave one warning in a log nobody
    # reads, and the anime sat at "No release yet" with nothing saying why.
    "RELEASE_LOOKUP_FAILED",
    # An anime moved to another library by the user (build 192).
    "LIBRARY_MOVED",
    # Shoko filed an anime's file in a different library than AniFlow chose
    # (build 192) - usually its Move on Import setting; AniFlow follows the
    # file and says so.
    "LIBRARY_MOVED_BY_SHOKO",
    # A finished torrent nobody had a row for: taken on, or reported.
    "TORRENT_ADOPTED",
    "TORRENT_UNCLAIMED",
)

#: The user made a decision, and this is what came of it.
DECISION_KINDS = (
    "DOWNLOAD_APPROVED",
    "DUPLICATE_RESOLVED",
    "ASSIGN_MATCHED",
    "ASSIGN_REJECTED",
)

#: Something about the installation rather than about one anime.
SYSTEM_KINDS = (
    # An add that did not happen, and why (build 140). Here rather than in
    # EPISODE_KINDS because there is no anime row to hang it on - the add is
    # what failed - and it is the one failure a user was watching for when it
    # happened: *"maybe [errors should] appear in the activity window"*.
    "ADD_FAILED",
    "INDEXER_MISSING",
    "INDEXER_UNREACHABLE",
    # The half-hourly pass that compares the database against disk. Kept
    # because unlike the other passes it reports *discrepancies it fixed*,
    # which is news rather than bookkeeping.
    "RECONCILED",
)

ALL_KINDS = EPISODE_KINDS + DECISION_KINDS + SYSTEM_KINDS

#: Removed in build 125, and listed so the reason survives the deletion.
#: Every one was logged once per *pass* rather than per episode, said nothing
#: a person could act on, and duplicated what the status boxes already show.
RETIRED_KINDS = ("SCAN_COMPLETED", "SYNC_PASSED", "QUEUE_PASSED", "IMPORT_PASSED")


__all__ = [
    "ALL_KINDS",
    "DECISION_KINDS",
    "EPISODE_KINDS",
    "RETIRED_KINDS",
    "SYSTEM_KINDS",
]

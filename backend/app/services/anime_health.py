"""How complete one anime is, in four groups (build 120).

This replaces the two-colour ``aniflow | shoko`` line with four icons, each
answering one question, and it is the single place both the icons and the
Debug windows read from. Asked for with the reasoning to do it properly:
*"Now its a good time to do a really proper thinking about this part and sort
and group them correctly."*

**metadata** - what is known *about the show*: its names, its cover, how many
episodes it has, when it airs, the ids that identify it elsewhere. Facts from
a provider or from AniDB. Nothing here depends on owning a single file.

**download** - what is needed to *fetch* episodes: the name releases are
published under, a subgroup, a quality, a feed source, and a magnet for each
episode that has aired.

**storage** - where the files *are*: the library folder, the hand-over folder
in Shoko Server mode, and whether the files the episodes claim are really on
disk under those names.

**shoko** - what the other library says, when there is one.

Why those four and not the fields as the database lists them: the database
groups by *when a value was written*, and a person reading a card wants to
know *which part is broken*. A missing cover and a missing magnet are both
"null in the anime row" and they are not remotely the same problem - one is
cosmetic, the other is why nothing is downloading.

Four colours, not three
-----------------------

``green``   everything this group needs is there.
``yellow``  something is missing that can be lived with.
``red``     something essential is missing, or most of it is.
``grey``    **not applicable here.**

The fourth one is the answer to the question the request asked: *"How this
works in basic mode where user do not have the download compatibility, but
they -could- have files on the disk."*  In that mode there is no indexer and
no release to configure, so a red download icon would be permanent and
blameless - the same mistake build 118 had to undo when every Shoko half went
red. Grey says "this does not apply to you", which is a different statement
from "this is wrong", and it keeps the other colours meaning something.

So, by mode:

===============  ========  ========  ========  =====
mode             metadata  download  storage   shoko
===============  ========  ========  ========  =====
standalone       judged    judged    judged    grey
shoko server     judged    judged    judged    judged
basic entry      judged    grey      judged*   judged
future entry     judged    grey      grey      judged
===============  ========  ========  ========  =====

A future entry (build 121) is one added before its season starts: it has no
releases to configure and no files, so two groups do not apply - and unlike a
basic entry, both start applying on a known date.

\\* storage is still judged for a basic entry, because the files may well be
there - that is exactly the case the request raised.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle, Episode, EpisodeStatus, EpisodeType

logger = for_component("Library")

GREEN, YELLOW, RED, GREY = "green", "yellow", "red", "grey"

#: The four groups, in the order the card shows them.
GROUPS = ("metadata", "download", "storage", "shoko")


@dataclass
class GroupState:
    """One icon's colour, and everything the Debug window needs to say."""

    colour: str = GREY
    #: Codes for what is missing. The browser puts words to them, as
    #: everywhere else - English from here would land on a German screen.
    missing: list[str] = field(default_factory=list)
    #: What *is* there, as (code, value) pairs. The Debug window shows both
    #: halves: "what is missing" is only readable beside what was found.
    known: list[tuple[str, str]] = field(default_factory=list)
    #: Why the group does not apply, when it is grey.
    not_applicable: str | None = None
    #: Codes for things that are true, fine, and worth reading anyway
    #: (build 123). A value that is absent *because it was never needed* is
    #: neither missing nor found: calling it missing puts a warning on a
    #: healthy anime, and saying nothing leaves the reader looking for an
    #: entry that is not there. Notes carry no colour - nothing here can
    #: make a group yellow.
    notes: list[str] = field(default_factory=list)


@dataclass
class AnimeHealth:
    metadata: GroupState = field(default_factory=GroupState)
    download: GroupState = field(default_factory=GroupState)
    storage: GroupState = field(default_factory=GroupState)
    shoko: GroupState = field(default_factory=GroupState)

    def as_dict(self) -> dict[str, dict]:
        return {
            name: {
                "colour": state.colour,
                "missing": list(state.missing),
                "known": [{"field": code, "value": value} for code, value in state.known],
                "not_applicable": state.not_applicable,
                "notes": list(state.notes),
            }
            for name, state in (
                ("metadata", self.metadata),
                ("download", self.download),
                ("storage", self.storage),
                ("shoko", self.shoko),
            )
        }


def _verdict(missing: list[str], essential: set[str]) -> str:
    """Green, yellow or red, on the rule the request gave.

    *"If all the data is there it will be a green icon, if something is
    missing it will be yellow, if everything or a big chunk is missing it will
    be red."*  So: nothing missing is green; a missing essential - or several
    things at once - is red; anything else is yellow.
    """
    # Nice to have, and nothing more (build 157): still reported, so the Edit
    # dialog can offer to fill them, but never a warning - *"this is really
    # nice to have and should not cause any warning to errors for the user."*
    missing = [code for code in missing if code not in NICE_TO_HAVE]
    if not missing:
        return GREEN
    if any(code in essential for code in missing):
        return RED
    return YELLOW if len(missing) < 4 else RED


#: Gaps below amber (build 157). Chosen by the user from the full list of
#: checks: studio and synopsis. Genres stay amber.
NICE_TO_HAVE = frozenset({"studios", "synopsis"})


# --- metadata ---------------------------------------------------------------

#: Without these an entry cannot do its job: it cannot be named on screen,
#: matched to a release, counted, or recognised in another database.
METADATA_ESSENTIAL = {"display_name", "episode_count", "cover"}


def metadata_state(anime: Anime) -> GroupState:
    """What is known about the show itself."""
    state = GroupState()

    def check(code: str, value) -> None:
        if value:
            state.known.append((code, str(value)[:200]))
        else:
            state.missing.append(code)

    check("display_name", anime.display_name)
    check("title_romaji", anime.title_romaji)
    check("title_english", anime.title_english)
    check("title_native", anime.title_native)
    check("cover", anime.cover_url or anime.custom_cover)
    # The values the anime actually uses - the user's own when they set one
    # (build 155). Reading the provider's columns kept "the airing time is
    # not set" up after the user had set it, saved it and reloaded.
    check("episode_count", anime.effective_episode_count)
    check("format", anime.format)
    check("air_start_date", anime.air_start_date)
    # A weekly slot matters while a show airs (build 156). A 1998 series will
    # never have one, and a check that can never close teaches the reader to
    # ignore the checks.
    if anime.effective_air_slot_at or not _finished_airing(anime):
        check("air_slot", anime.effective_air_slot_at)
    # The ids that let two databases talk about the same show. The AniDB one
    # is the id Shoko states and the numbering resolver walks chains by, so
    # it is worth naming separately from the rest.
    check("anidb_id", anime.anidb_id or (anime.provider_ids or {}).get("anidb"))
    check("anidb_titles", (anime.anidb_titles or {}).get("main"))
    check("provider_ids", len(anime.provider_ids or {}) > 1 or None)
    raw = anime.metadata_json or {}
    check("genres", raw.get("genres"))
    check("studios", raw.get("studios"))
    check("synopsis", raw.get("synopsis") or raw.get("description"))

    state.colour = _verdict(state.missing, METADATA_ESSENTIAL)
    return state


# --- download ---------------------------------------------------------------

DOWNLOAD_ESSENTIAL = {"release_name", "subgroup"}


def download_state(session: Session, anime: Anime, *, has_indexer: bool) -> GroupState:
    """What is needed to fetch episodes - or grey, when nothing can be."""
    state = GroupState()
    if anime.releases_ignored:
        # Asked for, and answered as "all release hunting": *"AniFlow will
        # not scan or look for this file."*  Grey rather than green - nothing
        # here has been verified, it has been excused - and first, because a
        # deliberate choice outranks every other reason this group might have
        # for being a colour.
        state.colour = GREY
        state.not_applicable = "releases_ignored"
        return state
    if anime.entry_mode == "future":
        # Its first episode does not exist yet (build 121), so there is
        # nothing to match, nothing to fetch and nothing here to be wrong.
        # Unlike the two below, this one ends by itself: once the premiere
        # passes, the entry is offered for conversion and this group starts
        # being judged.
        state.colour = GREY
        state.not_applicable = "not_aired_yet"
        return state
    if anime.entry_mode == "basic" or not has_indexer:
        # The case the request asked about. A tracker entry has no release to
        # configure and no indexer to ask, so there is nothing here to be
        # wrong - and a red icon would say otherwise for ever.
        state.colour = GREY
        state.not_applicable = "no_indexer" if not has_indexer else "tracker_entry"
        return state

    profile = getattr(anime, "profile", None)

    def check(code: str, value) -> None:
        if value:
            state.known.append((code, str(value)[:200]))
        else:
            state.missing.append(code)

    check("release_name", anime.release_name)
    check("subgroup", ", ".join(getattr(profile, "subgroups", None) or []) or None)
    check("resolution", getattr(profile, "resolution", None))
    # No feed source check since build 152. A scan reads every enabled feed
    # and matches each item against every anime, so the per-anime feed is
    # never consulted - a warning about it could not be fixed and would not
    # have mattered if it were.

    # A magnet for every episode that has aired, which is the part that
    # actually decides whether anything can be downloaded tonight.
    due = _aired(list(anime.episodes))
    without = [
        episode.number
        for episode in due
        if episode.status is not EpisodeStatus.COMPLETE and not episode.releases and not episode.skip_reason
    ]
    if without:
        state.missing.append("releases_for_aired_episodes")
        state.known.append(
            ("episodes_without_a_release", ", ".join(str(number) for number in without[:20]))
        )
    elif due:
        state.known.append(("episodes_with_a_release", str(len(due))))

    state.colour = _verdict(state.missing, DOWNLOAD_ESSENTIAL)
    if _waits_for_a_button(anime):
        # Amber and never more (build 212): nothing is broken, nothing will simply happen. Added after the
        # colour is worked out so it cannot tip a group over the "four gaps is red" line.
        state.missing.append("automation_off")
        if state.colour == GREEN:
            state.colour = YELLOW
    return state


def _waits_for_a_button(anime: Anime) -> bool:
    """Whether this anime still has episodes to get and AniFlow is not set to get them.

    Automate off is a choice, and for a finished show a perfectly good one - so only a show that is
    running (or about to) and still has an episode without its file is mentioned. Found by a card in
    In Progress that said "AniFlow is working on this" for an anime nothing was working on.
    """
    if anime.automation_enabled or anime.lifecycle not in (AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING):
        return False
    regular = [episode for episode in anime.episodes if episode.episode_type is EpisodeType.REGULAR]
    return not regular or any(
        episode.status is not EpisodeStatus.COMPLETE and not episode.skip_reason for episode in regular
    )


# --- storage ----------------------------------------------------------------


def storage_state(anime: Anime, *, shoko_mode: bool) -> GroupState:
    """Where the files are, and whether they are really there."""
    from backend.app.services.storage import media_path, roots_for, storage_roots

    state = GroupState()
    owned = [
        episode
        for episode in anime.episodes
        if episode.status is EpisodeStatus.COMPLETE or episode.media_filename
    ]
    if not owned:
        # Nothing has been placed yet, so there is nothing to be right or
        # wrong about. Grey rather than red: an anime added this morning has
        # no files and no fault.
        state.colour = GREY
        state.not_applicable = "nothing_owned"
        return state

    if anime.media_folder:
        state.known.append(("media_folder", anime.media_folder))
    else:
        # In Shoko Server mode the folder is Shoko's to name and AniFlow
        # learns it from the first file it recognises, so not knowing it yet
        # is a gap rather than a fault.
        state.missing.append("media_folder")
    if shoko_mode:
        # The importer creates this folder the first time it hands a file to
        # Shoko (services/importer.py). So there are three cases, and only
        # one of them is a fault:
        #
        # * it is known - the ordinary case;
        # * it is not known and nothing is waiting in it - every file this
        #   anime owns arrived some other way, which is what an adopted
        #   library looks like. Reported as a note (build 123);
        # * it is not known and an episode names a file inside it - then the
        #   folder really has been lost and the file cannot be reached.
        waiting = any(episode.import_filename for episode in anime.episodes)
        if anime.import_folder:
            state.known.append(("import_folder", anime.import_folder))
        elif waiting:
            state.missing.append("import_folder")
        else:
            state.notes.append("import_folder_unused")

    roots = roots_for(anime, storage_roots())
    gone: list[str] = []
    for episode in owned:
        if not episode.media_filename:
            gone.append(str(episode.number))
            continue
        target = media_path(roots, anime.media_folder, episode.media_filename)
        if target is None or not target.exists():
            gone.append(str(episode.number))
    if gone:
        state.missing.append("files_not_on_disk")
        state.known.append(("episodes_whose_file_is_gone", ", ".join(gone[:20])))
    else:
        state.known.append(("files_on_disk", str(len(owned))))

    # Two files for one episode (build 189): an original kept by a swap, or a
    # v1 next to a v2. Yellow - nothing is lost, somebody has to choose - and
    # the card's "!" is what tells them to go and look.
    from backend.app.services.superseded import DUPLICATE_KINDS

    doubled = [
        str(episode.number)
        for episode in owned
        if episode.media_filename
        and any(copy.removed_at is None and copy.kind in DUPLICATE_KINDS for copy in episode.copies)
    ]
    if doubled:
        state.missing.append("duplicate_files")
        state.known.append(("episodes_with_two_files", ", ".join(doubled[:20])))

    state.colour = _verdict(state.missing, {"files_not_on_disk"})
    return state


# --- shoko ------------------------------------------------------------------


def shoko_state(anime: Anime, fact, *, answered: bool, matched_by_name: bool) -> GroupState:
    """What the other library says about this anime."""
    state = GroupState()
    if not answered:
        state.colour = GREY
        state.not_applicable = "shoko_not_answered"
        return state
    if fact is None:
        state.colour = GREY
        state.not_applicable = (
            "no_anidb_id" if not (anime.anidb_id or (anime.provider_ids or {}).get("anidb"))
            else "not_in_shoko"
        )
        return state

    state.known.append(("shoko_series", str(getattr(fact, "name", "") or "")[:200]))
    state.known.append(("shoko_files", str(getattr(fact, "local_episodes", 0))))
    state.known.append(("shoko_episodes", str(getattr(fact, "total_episodes", 0))))

    missing_aired = int(getattr(fact, "missing_aired", 0) or 0)
    if missing_aired:
        state.missing.append("shoko_missing_aired")
        # Which ones, and when they aired (build 123). Reported as *"Shoko
        # has 10 files, AniFlow has 10 episodes ... so this must mean that
        # AniDB has episodes which are not aired yet?"* - the count alone
        # cannot answer that, and the numbers and dates can: these episodes
        # aired on these days and nobody has them.
        # The same test services/shoko_library.py counted them with, so the
        # list can never disagree with the number beside it.
        today = datetime.now(UTC).date().isoformat()
        named = [
            f"{number} ({str(when)[:10]})"
            for number, when in (getattr(fact, "missing", ()) or ())
            if when and str(when)[:10] < today
        ]
        if named:
            state.known.append(("shoko_missing_episodes", ", ".join(named[:20])))
    if matched_by_name:
        # Matched, but on a name rather than an id - which is right often
        # enough to show and not certain enough to be quiet about.
        state.missing.append("matched_by_name_only")

    total = int(getattr(fact, "total_episodes", 0) or 0)
    # The count the anime actually uses - the user's when they set one
    # (build 153). The provider's column alone kept this raised after the
    # user had already corrected the number.
    known = int(anime.effective_episode_count or 0)
    if total and known and total > known:
        state.missing.append("fewer_episodes_than_anidb")
        state.known.append(("episode_count", str(known)))

    state.colour = _verdict(state.missing, {"shoko_missing_aired"})
    return state


# --- the four together ------------------------------------------------------


def _finished_airing(anime: Anime) -> bool:
    """Whether this show must have finished by now, from its own numbers.

    Its premiere plus one week per episode, plus a month for breaks. Without
    both a premiere and an episode count nothing is assumed - a long runner
    with no count is exactly the show that still needs its slot.
    """
    start = anime.air_start_date
    count = anime.effective_episode_count
    if not start or not count:
        return False
    ends = start + timedelta(weeks=int(count), days=30)
    return ends < datetime.now(UTC).date()


#: How long after its broadcast an episode may go without a release before
#: the Download check mentions it (build 153). Subgroups need hours, not
#: minutes, and a warning that appears at air time and goes away on its own
#: by evening teaches the reader that warnings do not mean anything.
RELEASE_GRACE = timedelta(days=1)


def _aired(episodes: list[Episode]) -> list[Episode]:
    """The regular episodes that exist by now, and have had a day to appear.

    The date when there is one; otherwise the shape of the list - nothing
    below the highest episode somebody owns can still be unaired. Specials are
    left out: nothing matches releases for them, so none would ever have one.
    """
    now = datetime.now(UTC) - RELEASE_GRACE
    episodes = [ep for ep in episodes if ep.episode_type is EpisodeType.REGULAR]
    owned = [ep.number for ep in episodes if ep.status is EpisodeStatus.COMPLETE]
    highest = max(owned) if owned else 0
    due: list[Episode] = []
    for episode in episodes:
        when = episode.aired_at
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when <= now:
                due.append(episode)
            continue
        if episode.number <= highest:
            due.append(episode)
    return due


def _flag_uncertain_anidb(anime: Anime, state: GroupState) -> None:
    """Amber when the AniDB id is a guess or is contradicted (build 229).

    Only asked for one anime at a time (the Edit window and the detail page):
    the answer is a few lookups in the calendar store, which is nothing for
    one show and a lot for every card of a watchlist on every refresh. Added
    after the colour is worked out and never more than amber, like
    ``automation_off``: the id may well be right, and the point is to say it
    has not been checked.
    """
    from backend.app.services.anidb_confirmation import CONTRADICTED, UNCONFIRMED, verdict

    answer = verdict(anime)
    if answer.state == UNCONFIRMED:
        state.missing.append("anidb_unconfirmed")
    elif answer.state == CONTRADICTED:
        state.missing.append("anidb_contradicted")
    else:
        return
    state.known.append(("anidb_reason", answer.reason))
    if state.colour == GREEN:
        state.colour = YELLOW


def health_for(
    session: Session,
    anime: Anime,
    *,
    holdings: dict | None = None,
    by_title: dict | None = None,
    answered: bool = False,
    has_indexer: bool | None = None,
    shoko_mode: bool | None = None,
    check_anidb: bool = False,
) -> AnimeHealth:
    """All four groups for one anime.

    The Shoko answer is passed in rather than fetched: a page load must never
    wait on somebody else's server, and one walk of its library serves every
    card. See services/integration_state.py.
    """
    from backend.app.core.mode import AppMode, current_mode
    from backend.app.services.app_mode import has_indexer as any_indexer

    if shoko_mode is None:
        shoko_mode = current_mode() is AppMode.SHOKO_SERVER
    if has_indexer is None:
        try:
            has_indexer = any_indexer(session)
        except Exception:  # noqa: BLE001 - a missing folder is "no indexer"
            has_indexer = False

    fact, matched_by_name = _shoko_fact(anime, holdings or {}, by_title or {})
    metadata = metadata_state(anime)
    if check_anidb:
        _flag_uncertain_anidb(anime, metadata)
    return AnimeHealth(
        metadata=metadata,
        download=download_state(session, anime, has_indexer=bool(has_indexer)),
        storage=storage_state(anime, shoko_mode=bool(shoko_mode)),
        shoko=shoko_state(
            anime, fact, answered=answered, matched_by_name=matched_by_name
        ),
    )


def _shoko_fact(anime: Anime, holdings: dict, by_title: dict):
    """What Shoko says about this anime, by id and then by name."""
    from backend.app.services.anidb_titles import normalize

    aid = anime.anidb_id
    if aid is None:
        raw = (anime.provider_ids or {}).get("anidb")
        aid = int(raw) if str(raw or "").isdigit() else None
    if aid is not None and aid in holdings:
        return holdings[aid], False
    for title in (
        anime.display_name,
        anime.local_name,
        anime.title_romaji,
        anime.title_english,
        (anime.anidb_titles or {}).get("main"),
    ):
        if not title:
            continue
        found = by_title.get(normalize(title))
        if found is not None and found in holdings:
            return holdings[found], True
    return None, False


__all__ = [
    "GREEN",
    "GREY",
    "GROUPS",
    "RED",
    "YELLOW",
    "AnimeHealth",
    "GroupState",
    "health_for",
]

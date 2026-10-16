"""Filling in what a watchlist entry never learned about itself (build 123).

Reported after looking at the new Metadata icon across a real library:
*"Some anime have missing genres, studios and synopsis, and some are even
missing the airing time. We need to make sure that (1) when the user adds an
anime it will get all the meta data, (2) fix the ones which are already in the
watch list. It needs to be reliable for a new user."*

Why an entry ends up short
-------------------------

Not because the provider was asked and refused. Because of *how it arrived*:

* adopted from Shoko Server (builds 117-119), where the entry is built from
  the downloaded calendar or, failing that, from AniDB's title dump - which is
  names and nothing else;
* added from a search result on a source whose list endpoint returns less than
  its detail endpoint;
* added before a field existed at all. ``studios`` and ``synopsis`` arrived in
  build 101; every anime added before then has neither and never will, because
  nothing was ever going to go back and ask.

So this is a backfill in the same shape as services/schedule_backfill.py - a
handful per worker cycle, converging quietly - plus a button for when somebody
is looking straight at one entry and wants it fixed now.

Normalised, not raw
-------------------

The gap that made this hard to fix properly: ``metadata_json`` held whatever
shape the provider sent. AniList writes ``description`` and a nested
``studios: {nodes: [...]}``; Kitsu, Jikan, Shikimori and MyAnimeList each
write something else; the AniDB dump writes nothing. Anything reading it had
to know which source it came from, and services/anime_health.py - which is
what put the icon on the card - only knew AniList's.

Every adapter already normalises those fields onto the candidate, because that
is what :class:`AnimeCandidate` is for. This writes *those* into
``metadata_json`` under three fixed keys, beside the raw payload rather than
instead of it. One shape to read, whatever answered.

What it will not touch
----------------------

The user's own edits. Asked directly, and answered: *"Update, but never your
own edits ... I think this is fine, but it should notify the user maybe in the
activity window. With a Fix button to allow the change or Skip the change and
keep the user modification."*

That turns out to need no special care for the writing half, because the
schema already separates the two: ``episode_count`` is the provider's answer
and ``episode_count_override`` is the user's, and everything that matters
reads the override first. Refreshing the provider column cannot destroy an
override; it can only start *disagreeing* with one. So the notification is not
a guard against damage - it is the news that the two have parted company, with
one button to take the new value and Dismiss to keep yours.

``local_name``, ``display_name`` and ``custom_cover`` are never written here
at all. Those are not a provider's to have an opinion about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.models.activity import AttentionKind
from backend.app.models.anime import Anime, AnimeLifecycle
from backend.app.services import anidb_titles

logger = for_component("Metadata")

#: How many anime to look up per worker pass. The same number and the same
#: reasoning as the schedule backfill: AniList allows about ninety requests a
#: minute, nobody is waiting for this, and a library of two hundred converges
#: in a few minutes without ever looking like a burst.
BATCH_SIZE = 5

#: The three facts this module normalises into ``metadata_json``. Everything
#: else a candidate carries has a column of its own.
NORMALISED = ("genres", "studios", "synopsis")

#: What a metadata provider can actually answer for. The Metadata group
#: reports more than this - AniDB ids and titles come from the title dump
#: (services/anidb_identity.py) and the display name is the user's - and a
#: pass that counted those as its own work would never believe it was done.
FILLABLE = frozenset(
    {
        "genres", "studios", "synopsis", "cover", "episode_count", "format",
        "title_romaji", "title_english", "title_native",
        "air_start_date", "air_slot",
    }
)


#: Where the fields the user filled in by hand are remembered (build 152):
#: a list under this key in ``metadata_json``. The same JSON map as the raw
#: payload rather than a column per field - six booleans are not worth a
#: migration, and ``merged_metadata`` carries unknown keys through untouched.
MANUAL_KEY = "_manual"


def manual_fields(anime: Anime) -> set[str]:
    raw = anime.metadata_json if isinstance(anime.metadata_json, dict) else {}
    listed = raw.get(MANUAL_KEY)
    return {str(name) for name in listed} if isinstance(listed, list) else set()


def mark_manual(anime: Anime, column: str, manual: bool) -> None:
    """Remember, or forget, that the user set ``column`` themselves."""
    names = manual_fields(anime)
    names = names | {column} if manual else names - {column}
    raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
    raw[MANUAL_KEY] = sorted(names)
    # Reassigned, not mutated: SQLAlchemy does not see a change inside JSON.
    anime.metadata_json = raw


@dataclass
class Conflict:
    """A value the user set by hand that the provider now disagrees with."""

    field: str
    #: What the user set.
    mine: str
    #: What the provider says today.
    theirs: str


@dataclass
class Applied:
    """What one refill changed."""

    filled: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    #: Gaps that are still gaps: the provider does not know either.
    unanswered: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.filled)


def normalised(candidate: AnimeCandidate) -> dict[str, object]:
    """The three fields, in one shape, from whichever source answered."""
    return {
        "genres": [str(name) for name in (candidate.genres or ()) if name],
        "studios": [str(name) for name in (candidate.studios or ()) if name],
        "synopsis": candidate.synopsis or None,
    }


def merged_metadata(existing: object, candidate: AnimeCandidate) -> dict[str, object]:
    """``metadata_json`` with the normalised fields written over the raw.

    The raw payload is kept: it is the only record of what the source actually
    said, and the fields nobody has needed yet live in it.
    """
    merged: dict[str, object] = dict(existing if isinstance(existing, dict) else {})
    mine = merged.get(MANUAL_KEY)
    merged.update(candidate.raw_metadata or {})
    if isinstance(mine, list):
        # Never taken from a raw payload that happens to use the same key.
        merged[MANUAL_KEY] = mine
    for key, value in normalised(candidate).items():
        # What the user typed in stays theirs (build 156).
        if isinstance(mine, list) and key in mine:
            continue
        # An empty answer does not overwrite a full one. A source that does
        # not publish studios should not be able to delete the ones another
        # source already gave, and this runs against several sources over an
        # anime's life.
        if value or not merged.get(key):
            merged[key] = value
    return merged


def gaps(anime: Anime) -> list[str]:
    """Which of this anime's metadata gaps a provider could close.

    Read from services/anime_health.py rather than restated here: the icon on
    the card and the pass that fixes it must not be able to disagree about
    what "missing" means.
    """
    from backend.app.services.anime_health import metadata_state

    return [code for code in metadata_state(anime).missing if code in FILLABLE]


#: Lifecycles nobody is looking after. Asking about a dropped show spends a
#: request on an answer nothing will read.
_FORGOTTEN = (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED)

#: How long before an entry that still has a gap is asked again. Some gaps
#: are permanent - a 1996 series has no broadcast time and never will - so
#: "still missing" cannot be the whole trigger or the pass never finishes.
RETRY_GAPS_AFTER = timedelta(days=7)

#: How long before a *complete* entry is asked again. This is the half the
#: request was explicit about: *"if something changes it needs to update
#: existing infos"* - an episode count corrected upstream, a synopsis
#: rewritten, a cover replaced. Monthly, because none of that is urgent and
#: the point is to be a quiet guest.
RECHECK_AFTER = timedelta(days=30)


def due_for_metadata(anime: Anime, *, now: datetime | None = None) -> bool:
    """Whether this anime is worth a request right now."""
    if anime.lifecycle in _FORGOTTEN:
        return False
    last = anime.metadata_checked_at
    if last is None:
        # Never asked. Every row starts here, which is what makes the first
        # few worker passes after an upgrade do the whole backfill.
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    return moment - last >= (RETRY_GAPS_AFTER if gaps(anime) else RECHECK_AFTER)


def anime_needing_metadata(session: Session, *, limit: int = BATCH_SIZE) -> list[Anime]:
    """The next few anime to ask about, oldest first.

    The test is Python rather than SQL - it reads ``metadata_json`` and
    several columns - so this walks the watchlist and stops once it has a
    batch. A watchlist is hundreds of rows, not millions.
    """
    found: list[Anime] = []
    for anime in session.scalars(
        select(Anime).where(Anime.lifecycle.notin_(_FORGOTTEN)).order_by(Anime.created_at)
    ):
        if due_for_metadata(anime):
            found.append(anime)
        if len(found) >= limit:
            break
    return found


def provider_id_for(anime: Anime, provider_name: str) -> str | None:
    """Which id to ask this provider about, or None if it has never heard of it.

    An id belongs to the provider that issued it - AniList's 52991 and Kitsu's
    are different shows - so the primary id is only usable when the primary
    source *is* this provider. Otherwise the answer is in ``provider_ids``,
    which the adoption and search paths fill in when a source names another
    source's id. Guessing is how a library ends up describing the wrong show.
    """
    if anime.primary_provider == provider_name:
        return anime.primary_provider_id
    ids = anime.provider_ids or {}
    # MyAnimeList's ids go by three names (build 154): the adapters write
    # "myanimelist", the calendar "mal", and Jikan is the same catalogue.
    names = _SAME_CATALOGUE.get(provider_name, (provider_name,))
    for name in names:
        if ids.get(name):
            return str(ids[name])
    return None


_SAME_CATALOGUE = {
    "mal": ("mal", "myanimelist", "jikan"),
    "myanimelist": ("myanimelist", "mal", "jikan"),
    "jikan": ("jikan", "mal", "myanimelist"),
}


def link_ids(anime: Anime) -> list[str]:
    """Give this entry every id the offline mapping list knows (build 154).

    What made an adopted entry unreachable: it held an AniDB id, and every
    provider that can describe a show wants its own. The list closes that in
    one local lookup. Only ids the entry does not have are added - one it
    holds may have been corrected by hand - and an AniDB id goes through
    ``anidb_identity.adopt`` so its names come with it.
    """
    from backend.app.services import id_mapping

    known = {k: str(v) for k, v in (anime.provider_ids or {}).items() if v}
    if anime.primary_provider and anime.primary_provider_id:
        known.setdefault(anime.primary_provider, str(anime.primary_provider_id))
    if anime.anidb_id:
        known.setdefault("anidb", str(anime.anidb_id))
    found = id_mapping.ids_for_anime(known)
    # The id list says "mal"; the entry says "myanimelist" (see
    # canonical_provider_ids), so it is asked about under the name it is kept by.
    found = {("myanimelist" if name == "mal" else name): value for name, value in found.items()}
    added = [name for name, value in found.items() if name not in known and not (name == "myanimelist" and known.get("mal"))]
    if not added:
        return []
    anime.provider_ids = {**(anime.provider_ids or {}), **{name: found[name] for name in added}}
    if "anidb" in added and not anime.anidb_id:
        from backend.app.services.anidb_identity import adopt

        adopt(anime, int(found["anidb"]))
    return added


def park(candidate: AnimeCandidate) -> None:
    """Keep what a provider said in the one store (build 154). Never raises.

    *"it should try to do a sync like it does currently in the calendar and
    get the anime infos directly from some sources, but should park this info
    somewhere, which comes back again to a single database."*  So every
    answer a provider gives about a watchlist entry is written into the
    calendar's store as well - the next entry, the next install of the same
    show, the calendar's own pages all find it there.
    """
    from backend.app.db import calendar_store
    from backend.app.services.calendar_snapshot import apply_one

    try:
        with calendar_store.sessions()() as session:
            apply_one(session, candidate)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - the watchlist's refill must not fail on this
        logger.warning("could not keep %s in the calendar store: %s", candidate.display_name, exc)


def fill_english_from_anidb(anime: Anime) -> bool:
    """An English title from AniDB's own names when no provider had one (build 237).

    Many shows have no official English title anywhere, but AniDB files an
    English synonym for them. Never replaces a title that is there or one the
    user typed, and never asks anything: it reads the weekly dump.
    """
    if anime.title_english or "title_english" in manual_fields(anime):
        return False
    aid = anime.anidb_id or (anime.provider_ids or {}).get("anidb")
    try:
        names = anidb_titles.names_for(int(aid)) if aid else {}
    except (TypeError, ValueError):
        return False
    english = names.get("en")
    if not english:
        return False
    anime.title_english = english
    return True


def apply_candidate(anime: Anime, candidate: AnimeCandidate) -> Applied:
    """Write what the provider answered, and report what it changed.

    Values are written whether or not they were missing - *"it should update
    existing infos"* - and the two columns a user can override are compared
    rather than obeyed.
    """
    result = Applied()
    before = set(gaps(anime))

    merged = merged_metadata(anime.metadata_json, candidate)
    if merged != (anime.metadata_json or {}):
        anime.metadata_json = merged

    # What the user typed into the Edit dialog stays theirs (build 152).
    mine = manual_fields(anime)
    for column, value in (
        ("title_romaji", candidate.title_romaji),
        ("title_english", candidate.title_english),
        ("title_native", candidate.title_native),
        ("cover_url", candidate.cover_url),
        ("format", candidate.format),
        ("air_start_date", candidate.air_start_date),
        ("air_slot_at", candidate.air_slot_at),
    ):
        if value and column not in mine:
            setattr(anime, column, value)
    if candidate.aliases:
        anime.aliases = list(candidate.aliases)
    if candidate.provider_ids:
        anime.provider_ids = {**(anime.provider_ids or {}), **candidate.provider_ids}

    fill_english_from_anidb(anime)

    # The episode count is the one value with a user-owned twin, so it is the
    # one that can start an argument. Written anyway: the provider column is
    # the provider's, the override is the user's, and everything that matters
    # reads `effective_episode_count`.
    if candidate.episode_count:
        was = anime.episode_count
        anime.episode_count = candidate.episode_count
        if (
            anime.episode_count_is_manual
            and anime.episode_count_override != candidate.episode_count
        ):
            result.conflicts.append(
                Conflict(
                    field="episode_count",
                    mine=str(anime.episode_count_override),
                    theirs=str(candidate.episode_count),
                )
            )
        elif was and was != candidate.episode_count:
            result.filled.append("episode_count")

    if candidate.air_slot_at and anime.schedule_is_manual:
        mine = anime.air_slot_override_at
        # Same weekday and time is not a disagreement; a different instant of
        # the same slot is what a manual override is *for*.
        if mine is not None and (
            mine.weekday() != candidate.air_slot_at.weekday()
            or mine.timetz() != candidate.air_slot_at.timetz()
        ):
            result.conflicts.append(
                Conflict(
                    field="air_slot",
                    mine=mine.isoformat(timespec="minutes"),
                    theirs=candidate.air_slot_at.isoformat(timespec="minutes"),
                )
            )

    # Adult from what the provider says, if the user has not decided (build 198).
    from backend.app.services import adult_rule

    adult_rule.apply(anime, provider_flag=bool(getattr(candidate, "is_adult", False)))

    anime.metadata_checked_at = datetime.now(UTC)
    after = set(gaps(anime))
    # The gaps that closed, plus the corrections above - which are not gaps
    # at all, because a *wrong* episode count is present and wrong. Deduped
    # because a corrected count can be both.
    for code in sorted(before - after):
        if code not in result.filled:
            result.filled.append(code)
    result.unanswered = sorted(after & before)
    return result


def record_conflicts(session: Session, anime: Anime, result: Applied) -> None:
    """Put a disagreement in front of the user, with a way to settle it."""
    from backend.app.services.attention import raise_attention

    for conflict in result.conflicts:
        raise_attention(
            session,
            kind=AttentionKind.METADATA_CHANGED,
            anime_id=anime.id,
            # The field code first, so the record can be read by machine as
            # well as by eye: services/attention_review.py needs to know
            # *which* of the two overridable values this is about, and
            # `detail` is the only field it has to say it in.
            detail=(
                f"{conflict.field}: {anime.display_name} - "
                f"{anime.primary_provider} says {conflict.theirs}, "
                f"you set {conflict.mine}"
            ),
        )


def accept_provider_value(session: Session, attention_id: str) -> str:
    """Drop the override this record was about. Returns which one.

    "Allow the change" does not mean writing the provider's number somewhere:
    the provider's number is already in ``episode_count``, and the override is
    what is standing in front of it. So accepting is *removing* the override,
    after which the anime reads the provider again - and keeps reading it,
    which is the point of accepting rather than editing.
    """
    from backend.app.models.activity import Attention, AttentionStatus
    from backend.app.services.episode_state import refresh_anime_statuses

    record = session.get(Attention, attention_id)
    if record is None:
        raise LookupError("no such attention record")
    if record.kind is not AttentionKind.METADATA_CHANGED:
        raise LookupError("that record is not about a metadata change")
    anime = session.get(Anime, record.anime_id) if record.anime_id else None
    if anime is None:
        raise LookupError("the anime this was about is gone")

    field = (record.detail or "").split(":", 1)[0].strip()
    if field == "episode_count":
        anime.episode_count_override = None
        # The rows follow the count: accepting 13 where 12 was set has to
        # create episode 13, or the new number is a claim with nothing behind
        # it. Trimming is deliberately not done - a row somebody owns a file
        # for is not deleted because a provider revised a number.
        from backend.app.services.anime import ensure_episode_rows

        ensure_episode_rows(session, anime, anime.episode_count)
        refresh_anime_statuses(session, anime.id)
    elif field == "air_slot":
        anime.air_slot_override_at = None
    else:
        raise LookupError("that record does not name a value to accept")

    record.status = AttentionStatus.RESOLVED
    record.resolved_at = datetime.now(UTC)
    record.note = f"{(record.note or '').strip()} [accepted the provider's value]".strip()
    logger.info("accepted the provider's %s for %s", field, anime.display_name)
    return field


# --- the calendar first (build 153) -----------------------------------------
#
# Reported against 152: *"all of the animes in the watchlist show missing
# metadata studio, synopsis ... If I look at the Calendar. It has every
# metadata."*  Right, and the reason is that there were two stores that never
# spoke. The calendar (data/calendar.db) is filled from five providers and
# keeps genres, studios and a synopsis for every show it has seen. The
# watchlist entry keeps its own copy, filled once when it was added - and an
# entry adopted from Shoko Server or built from AniDB's title dump arrives
# with names and ids and nothing else. The Refill button could not help
# either: it asks the one provider chosen in Settings, *by that provider's
# id*, and an adopted entry has an AniDB id and no AniList one.
#
# So the calendar is asked first, by id - every id the entry has, exactly,
# never by name. It is local: no request, no pacer, nothing to be refused.
# And it only fills *gaps*: the calendar is a snapshot of a season, the
# provider is live, and a value somebody already has is not the calendar's to
# replace.


def calendar_candidate(anime: Anime) -> AnimeCandidate | None:
    """The calendar's record for this anime, found by one of its ids."""
    from backend.app.services import calendar_lookup

    try:
        if anime.primary_provider and anime.primary_provider_id:
            found = calendar_lookup.by_provider_id(anime.primary_provider, anime.primary_provider_id, proven_only=True)
            if found is not None:
                return found
        aid = anime.anidb_id or (anime.provider_ids or {}).get("anidb")
        if str(aid or "").isdigit():
            found = calendar_lookup.by_anidb_id(int(aid), proven_only=True)
            if found is not None:
                return found
        for provider, provider_id in (anime.provider_ids or {}).items():
            if provider == "anidb" or not provider_id:
                continue
            found = calendar_lookup.by_provider_id(provider, str(provider_id), proven_only=True)
            if found is not None:
                return found
    except Exception as exc:  # noqa: BLE001 - a local lookup must never break a refill
        logger.debug("calendar lookup failed for %s: %s", anime.display_name, exc)
    return None


def fill_from_calendar(anime: Anime) -> list[str]:
    """Close what gaps the calendar can. Returns the codes it closed."""
    before = set(gaps(anime))
    if not before:
        return []
    candidate = calendar_candidate(anime)
    if candidate is None:
        return []
    return close_gaps_from(anime, candidate)


def close_gaps_from(anime: Anime, candidate: AnimeCandidate) -> list[str]:
    """Write only what this anime is missing from ``candidate``. Returns the codes it closed.

    The calendar's record and a second provider's answer share this rule: neither may replace what an
    anime already holds.
    """
    before = set(gaps(anime))
    raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
    for key, value in normalised(candidate).items():
        if value and not raw.get(key):
            raw[key] = value
    if raw != (anime.metadata_json or {}):
        anime.metadata_json = raw
    from backend.app.services import adult_rule

    adult_rule.apply(anime, provider_flag=bool(getattr(candidate, "is_adult", False)))

    mine = manual_fields(anime)
    for column, value in (
        ("title_romaji", candidate.title_romaji),
        ("title_english", candidate.title_english),
        ("title_native", candidate.title_native),
        ("cover_url", candidate.cover_url),
        ("format", candidate.format),
        ("air_start_date", candidate.air_start_date),
        ("air_slot_at", candidate.air_slot_at),
    ):
        if value and not getattr(anime, column) and column not in mine:
            setattr(anime, column, value)
    if candidate.episode_count and not anime.episode_count:
        anime.episode_count = candidate.episode_count
    fill_english_from_anidb(anime)
    if candidate.provider_ids:
        # Only ids this entry does not have: an id it already holds may have
        # been corrected by hand or by Shoko. And only ids that name one show
        # (build 155) - a series-wide TVDB id is not this entry's.
        from backend.app.services.calendar_identity import names_one_show

        theirs = {k: v for k, v in candidate.provider_ids.items() if names_one_show(k, v)}
        merged = {**theirs, **(anime.provider_ids or {})}
        if merged != (anime.provider_ids or {}):
            anime.provider_ids = merged

    return sorted(before - set(gaps(anime)))


#: How often the whole watchlist is compared against the calendar. Local and
#: cheap, but the sync pass that calls this runs every thirty seconds, and a
#: gap the calendar cannot close either would be looked up again every time.
CALENDAR_PASS_EVERY = timedelta(minutes=30)
_last_calendar_pass: datetime | None = None


def fill_all_from_calendar(session: Session, *, now: datetime | None = None) -> int:
    """The calendar pass over the whole watchlist. Returns how many changed.

    Every entry with a gap, at most every ``CALENDAR_PASS_EVERY``: it costs a
    local query each, so there is no reason to ration it per entry the way
    the provider pass is rationed.
    """
    global _last_calendar_pass
    now = now or datetime.now(UTC)
    if _last_calendar_pass is not None and now - _last_calendar_pass < CALENDAR_PASS_EVERY:
        return 0
    _last_calendar_pass = now
    changed = 0
    for anime in session.scalars(select(Anime).where(Anime.lifecycle.not_in(_FORGOTTEN))):
        link_ids(anime)
        closed = fill_from_calendar(anime)
        if closed:
            changed += 1
            logger.info("filled in %s for %s from the calendar", ", ".join(closed), anime.display_name)
    return changed


async def _refill_from_sources(session: Session, anime: Anime) -> Applied | None:
    """Fill one anime in: from the calendar, then from a provider.

    The ids first (build 154): the offline mapping list gives an adopted
    entry the AniList or Kitsu id it never had, which is what lets the rest
    of this work at all. Then the store, then a provider - the one chosen in
    Settings when the entry has an id for it, otherwise any that it has an id
    for - and what the provider answers is kept in the store too (``park``).

    None when nothing could be asked. The calendar's half survives the
    provider failing - it is already written, and throwing it away because
    somebody else's server is down would be the wrong way round.
    """
    from backend.app.services.metadata import PROVIDERS, selected

    link_ids(anime)
    local = fill_from_calendar(anime)
    if fill_english_from_anidb(anime):
        local = sorted({*local, "title_english"})

    preferred = await selected(session)
    provider = PROVIDERS[preferred]
    provider_id = provider_id_for(anime, provider.name)
    if provider_id is None:
        for name, other in PROVIDERS.items():
            if name != preferred and provider_id_for(anime, name):
                provider, provider_id = other, provider_id_for(anime, name)
                break
    asked = {provider.name} if provider_id is not None else set()
    result: Applied | None
    if provider_id is None:
        # An entry built from AniDB's title dump, or added through a source
        # that is no longer selected. Not a failure: nothing here can invent
        # an id, and inventing one would describe the wrong show.
        anime.metadata_checked_at = datetime.now(UTC)
        result = _local_only(anime, local)
    else:
        try:
            candidate = await provider.get_anime(provider_id)
        except Exception:
            if local:
                logger.info("%s did not answer; kept what the calendar had", provider.name)
                return await _ask_the_others(anime, _local_only(anime, local), asked)
            raise
        if candidate is None:
            anime.metadata_checked_at = datetime.now(UTC)
            result = _local_only(anime, local)
        else:
            park(candidate)
            result = apply_candidate(anime, candidate)
            result.filled = sorted(set(result.filled) | set(local))
            record_conflicts(session, anime, result)
    return await _ask_the_others(anime, result, asked)


#: Kitsu's names for the other databases' ids, in the order they are tried.
_KITSU_SITES = (("myanimelist/anime", ("myanimelist", "mal")), ("anidb", ("anidb",)))


async def _kitsu_id_for(anime: Anime, kitsu) -> str | None:
    """Kitsu's id for this anime, asked of Kitsu's own cross-references by an id we hold (build 239)."""
    ids = anime.provider_ids or {}
    for site, names in _KITSU_SITES:
        held = next((str(ids[name]) for name in names if ids.get(name)), None)
        if held is None and site == "anidb" and anime.anidb_id:
            held = str(anime.anidb_id)
        if not held:
            continue
        try:
            found = await kitsu.find_by_external_id(site, held)
        except Exception as exc:  # noqa: BLE001 - somebody else's server
            logger.info("kitsu could not map %s %s: %s", site, held, exc)
            return None
        if found:
            return found
    return None


async def _ask_the_others(anime: Anime, result: Applied | None, asked: set[str]) -> Applied | None:
    """A gap the chosen source could not close is put to the other sources, by id (build 239).

    *"Fill in missing does still not fill the episode numbers ... I did not find anything in MAL, but on
    Kitsu it shows 12 episodes."* One source answering with nothing is not the end: each other source is
    asked in turn, only while something is still open, and only for what is missing - the chosen
    source's values are not overwritten. An id is looked up (Kitsu's mapping), never a name guessed.
    """
    from backend.app.services.metadata import PROVIDERS

    if not gaps(anime):
        return result
    closed: list[str] = []
    for name, other in PROVIDERS.items():
        if name in asked or not gaps(anime):
            continue
        provider_id = provider_id_for(anime, name)
        if provider_id is None and name == "kitsu":
            provider_id = await _kitsu_id_for(anime, other)
        if provider_id is None:
            continue
        asked.add(name)
        try:
            candidate = await other.get_anime(provider_id)
        except Exception as exc:  # noqa: BLE001 - somebody else's server
            logger.info("%s did not answer for %s: %s", name, anime.display_name, exc)
            continue
        if candidate is None:
            continue
        if name == "kitsu" and not (anime.provider_ids or {}).get("kitsu"):
            anime.provider_ids = {**(anime.provider_ids or {}), "kitsu": provider_id}
        park(candidate)
        closed.extend(close_gaps_from(anime, candidate))
    if not closed:
        return result
    result = result or Applied()
    result.filled = sorted(set(result.filled) | set(closed))
    result.unanswered = sorted(set(gaps(anime)))
    return result


def _local_only(anime: Anime, local: list[str]) -> Applied | None:
    if not local:
        return None
    return Applied(filled=list(local), unanswered=sorted(gaps(anime)))


async def _ask_shoko(anime: Anime, result: Applied | None) -> Applied | None:
    """What is still missing is put to Shoko Server, when there is one (build 263).

    *"Fill in missing should also check Shoko Server ... and get pictures and infos from Shoko if they are only
    available there."*  The last step, after the calendar, the chosen source and the other sources: Shoko keeps
    AniDB's own record of every show it has seen, so it can answer for the show none of them knew. Only what is
    missing is written (``close_gaps_from``), the poster is stored as this anime's own cover the way adoption does,
    and a Shoko that is not connected, or does not know the show, changes nothing.
    """
    import asyncio

    from backend.app.services import shoko_metadata

    if not gaps(anime):
        return result
    facts = await asyncio.to_thread(shoko_metadata.facts_for, anime)
    if facts is None:
        return result
    closed = close_gaps_from(anime, facts.candidate)
    if "cover" in gaps(anime):
        data = await asyncio.to_thread(shoko_metadata.poster_bytes, facts)
        if data:
            from backend.app.services.covers import CoverError, store_cover

            try:
                anime.custom_cover = store_cover(anime.id, data)
                closed.append("cover")
            except CoverError as exc:
                logger.debug("Shoko's poster for %s could not be kept: %s", anime.display_name, exc)
    if not closed:
        return result
    logger.info("filled in %s for %s from Shoko Server", ", ".join(sorted(closed)), anime.display_name)
    result = result or Applied()
    result.filled = sorted(set(result.filled) | set(closed))
    result.unanswered = sorted(set(gaps(anime)))
    return result


async def refill_one(session: Session, anime: Anime) -> Applied | None:
    """Fill one anime in: the calendar, the sources, and last Shoko Server.

    Shoko is asked even when the sources failed - a metadata server being down is exactly when the one on the
    local network is worth asking - and the failure is raised only when Shoko closed nothing either.
    """
    error: Exception | None = None
    result: Applied | None = None
    try:
        result = await _refill_from_sources(session, anime)
    except Exception as exc:  # noqa: BLE001 - raised again below, unless Shoko made up for it
        error = exc
    result = await _ask_shoko(anime, result)
    if error is not None and not (result and result.changed):
        raise error
    return result


async def refill_missing(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Fill in a few anime per pass. The worker's half of this."""
    session = session_factory()
    counts = {"considered": 0, "filled": 0, "unanswerable": 0, "failed": 0, "from_calendar": 0}
    try:
        # The calendar first, for everyone - local and free (build 153).
        counts["from_calendar"] = fill_all_from_calendar(session)
        session.commit()
        pending = anime_needing_metadata(session)
        counts["considered"] = len(pending)
        if not pending:
            return counts
        for anime in pending:
            try:
                result = await refill_one(session, anime)
            except Exception as exc:  # noqa: BLE001 - a chore must not break the cycle
                # One line, then stop for this pass: whatever stopped the
                # provider answering for this anime will stop it for the next
                # four, and five identical warnings a minute is not a log.
                logger.warning(
                    "could not refresh metadata (%s); leaving the rest for the next pass", exc
                )
                counts["failed"] += 1
                break
            if result is None:
                counts["unanswerable"] += 1
                continue
            if result.changed:
                counts["filled"] += 1
                logger.info(
                    "filled in %s for %s", ", ".join(result.filled), anime.display_name
                )
        session.commit()
        return counts
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "BATCH_SIZE",
    "accept_provider_value",
    "RECHECK_AFTER",
    "RETRY_GAPS_AFTER",
    "FILLABLE",
    "NORMALISED",
    "Applied",
    "Conflict",
    "anime_needing_metadata",
    "apply_candidate",
    "calendar_candidate",
    "link_ids",
    "park",
    "fill_all_from_calendar",
    "fill_from_calendar",
    "due_for_metadata",
    "gaps",
    "merged_metadata",
    "normalised",
    "provider_id_for",
    "refill_missing",
    "refill_one",
]

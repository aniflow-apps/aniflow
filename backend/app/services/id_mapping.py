"""Every id an anime has, from one offline list (build 154).

Asked for as: *"the data in the calendar.db needs to have ALL ids listed. For
example anidb id, anilist id, jikan id, kitsu id, mal id, shikimori id, etc. So
that there is never a case, where anime x can not link to anime x in the meta
data."*

Why the providers cannot give that on their own: AniList publishes a MAL id
and nothing else; Jikan, MyAnimeList and Shikimori only know MAL ids; Kitsu
knows the most, but only in its season listing; and AniDB publishes titles,
not links. So before this, an entry adopted from Shoko Server had an AniDB id
and no way to reach AniList, and an AniList show had its AniDB id only if the
title dump happened to agree on a name.

The list is the Fribb anime-lists file, which is built from the
manami-project offline database plus the providers' own links: one JSON array,
one object per anime, with ``anidb_id``, ``anilist_id``, ``mal_id``,
``kitsu_id`` and more. It is fetched at most weekly, like the AniDB dump, and
lives in ``calendar_id_map``. Shikimori has no column: its ids *are* MAL ids,
which is what the Shikimori adapter already assumes.

Two rules carried over from the rest of the calendar:

* **An outage never empties anything.** The table is replaced only by a
  complete, validated read of a new file; a failed download or a file that
  parses to almost nothing leaves the old rows exactly as they were.
* **A mapping never merges two Works.** Ids from the list are attached through
  ``calendar_identity.attach``, which refuses an id another Work already holds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.calendar import (
    EVIDENCE_MAPPING,
    EVIDENCE_TITLE,
    PROVEN,
    CalendarIdentity,
    CalendarIdMap,
    CalendarIdMapState,
    CalendarWork,
)

logger = for_component("Calendar")

#: The list itself. Plain JSON, a few megabytes, updated upstream about weekly.
MAPPING_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"

#: How often it is fetched. The same week the AniDB dump waits.
REFRESH_INTERVAL = timedelta(days=7)
#: How long after a failed attempt before trying again.
RETRY_INTERVAL = timedelta(hours=6)

#: Fewer rows than this and the file is not the list, whatever it parsed as.
#: The real one has tens of thousands; an error page or a truncated download
#: must not replace it.
MIN_ROWS = 5000

#: The file's keys, by the calendar's provider names.
_KEYS = {"anidb": "anidb_id", "anilist": "anilist_id", "mal": "mal_id", "kitsu": "kitsu_id"}
#: The providers this can answer for, by the name a caller may use.
_ALIASES = {
    "anidb": "anidb", "anilist": "anilist", "kitsu": "kitsu",
    "mal": "mal", "myanimelist": "mal", "jikan": "mal", "shikimori": "mal",
}
COLUMNS = tuple(_KEYS)


def _clean(value: object) -> str | None:
    """An id as the rest of the calendar stores it: digits, as a string."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text if text.isdigit() and text != "0" else None


def parse(payload: bytes) -> list[dict[str, str | None]]:
    """The file's rows that link at least two databases. Raises on a bad file."""
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("the id-mapping file is not a list")
    rows: list[dict[str, str | None]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = {column: _clean(item.get(key)) for column, key in _KEYS.items()}
        # One id on its own links nothing to anything.
        if sum(1 for value in row.values() if value) >= 2:
            rows.append(row)
    return rows


# --- the file ------------------------------------------------------------------


@dataclass
class MappingStatus:
    rows: int
    downloaded_at: datetime | None
    last_error: str | None


def _state(session: Session) -> CalendarIdMapState:
    state = session.get(CalendarIdMapState, 1)
    if state is None:
        state = CalendarIdMapState(id=1)
        session.add(state)
    return state


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def status() -> MappingStatus:
    if not calendar_store.exists():
        return MappingStatus(rows=0, downloaded_at=None, last_error=None)
    with calendar_store.sessions()() as session:
        state = session.get(CalendarIdMapState, 1)
        if state is None:
            return MappingStatus(rows=0, downloaded_at=None, last_error=None)
        return MappingStatus(rows=state.rows, downloaded_at=state.downloaded_at, last_error=state.last_error)


def refresh(*, force: bool = False, client: httpx.Client | None = None, now: datetime | None = None) -> MappingStatus:
    """Fetch the list if it is a week old, and load it. Never raises.

    Afterwards every existing Work is given the ids the list knows - once per
    new list, not on every call.
    """
    now = now or datetime.now(UTC)
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        logger.warning("id-mapping list not refreshed: %s", exc)
        return MappingStatus(rows=0, downloaded_at=None, last_error=str(exc))

    with sessions() as session:
        state = _state(session)
        if not force and state.downloaded_at and now - _as_utc(state.downloaded_at) < REFRESH_INTERVAL and state.rows:
            due_linking = state.linked_at is None or _as_utc(state.linked_at) < _as_utc(state.downloaded_at)
            session.commit()
            if due_linking:
                link_all_works()  # which also drops the guesses the list contradicts
            else:
                # Cheap, and a guess made since the last pass would otherwise wait a week.
                drop_contradicted_title_links()
            return status()
        if not force and state.last_error and state.attempted_at and now - _as_utc(state.attempted_at) < RETRY_INTERVAL:
            session.commit()
            return status()
        state.attempted_at = now
        session.commit()

    from backend.app.services.anidb_titles import user_agent
    from backend.app.services.outbound import client_kwargs

    owns = client is None
    http = client or httpx.Client(
        timeout=120.0, follow_redirects=True,
        headers={"User-Agent": user_agent()}, **client_kwargs(MAPPING_URL),
    )
    try:
        response = http.get(MAPPING_URL)
        response.raise_for_status()
        payload = response.content
        rows = parse(payload)
        if len(rows) < MIN_ROWS:
            raise ValueError(f"the id-mapping file held only {len(rows)} usable rows")
    except (httpx.HTTPError, ValueError) as exc:
        # The rows already loaded stay: an old list is worth far more than none.
        reason = str(exc) or type(exc).__name__
        logger.warning("could not refresh the id-mapping list: %s", reason)
        with sessions() as session:
            _state(session).last_error = reason[:400]
            session.commit()
        return status()
    finally:
        if owns:
            http.close()

    load(rows, size=len(payload), now=now)
    link_all_works()
    return status()


def load(rows: list[dict[str, str | None]], *, size: int = 0, now: datetime | None = None) -> None:
    """Replace the table with these rows, in one transaction."""
    now = now or datetime.now(UTC)
    with calendar_store.sessions()() as session:
        session.execute(delete(CalendarIdMap))
        session.bulk_insert_mappings(CalendarIdMap, rows)
        state = _state(session)
        state.downloaded_at = now
        state.rows = len(rows)
        state.bytes_downloaded = size
        state.last_error = None
        session.commit()
    logger.info("id-mapping list loaded: %s anime", len(rows))


# --- asking it -------------------------------------------------------------------


def ids_for(session: Session, provider: str, provider_id: str) -> dict[str, str]:
    """Every id the list knows for this one, by canonical provider name.

    Empty when the list does not know it. When it lists the id more than once
    (a split entry upstream), only the ids every row agrees on are returned -
    a disagreement is a question, and the answer to a question is not a guess.
    """
    column = _ALIASES.get((provider or "").strip().lower())
    wanted = _clean(provider_id)
    if column is None or wanted is None:
        return {}
    rows = session.scalars(select(CalendarIdMap).where(getattr(CalendarIdMap, column) == wanted).limit(5)).all()
    if not rows:
        return {}
    agreed: dict[str, str] = {}
    for name in COLUMNS:
        values = {getattr(row, name) for row in rows if getattr(row, name)}
        if len(values) == 1:
            agreed[name] = values.pop()
    return agreed


def ids_for_any(session: Session, known: dict[str, str]) -> dict[str, str]:
    """Every id reachable from any of these, if none of them disagree."""
    found: dict[str, str] = {}
    for provider, provider_id in known.items():
        for name, value in ids_for(session, provider, str(provider_id)).items():
            if found.get(name, value) != value:
                # Two of the known ids lead to different shows. Nothing is
                # safe to add from here.
                return {}
            found[name] = value
    # Never contradict what the caller already holds.
    for provider, provider_id in known.items():
        name = _ALIASES.get(provider.lower())
        if name and name in found and found[name] != str(provider_id):
            return {}
    return found


def link_all_works() -> int:
    """Give every Work the ids the list knows for it. Returns how many were added."""
    from backend.app.services.calendar_identity import Claim, attach

    added = 0
    with calendar_store.sessions()() as session:
        by_work: dict[str, dict[str, str]] = {}
        for identity in session.scalars(select(CalendarIdentity).where(CalendarIdentity.evidence.in_(sorted(PROVEN)))):
            by_work.setdefault(identity.work_id, {})[identity.provider] = identity.provider_id
        for work_id, known in by_work.items():
            work = session.get(CalendarWork, work_id)
            if work is None:
                continue
            for provider, provider_id in ids_for_any(session, known).items():
                if known.get(provider) == provider_id:
                    continue
                if attach(session, work, Claim(provider, provider_id, evidence=EVIDENCE_MAPPING, evidence_from="id-map")):
                    added += 1
        _state(session).linked_at = datetime.now(UTC)
        session.commit()
    if added:
        logger.info("id-mapping list: %s ids attached to existing shows", added)
    # Guesses the list now contradicts (see owned_by_another) - after the
    # attaching, so a show that has just been given its proven AniDB id keeps it.
    dropped = drop_contradicted_title_links()
    if dropped:
        logger.warning("id-mapping list: %s title-guessed AniDB ids belonged to other shows and were removed", dropped)
    return added


def owned_by_another(session: Session, provider: str, provider_id: str, proven: dict[str, str]) -> bool:
    """Whether the list says this id belongs to a *different* show than the one holding ``proven``.

    The other half of "a title guess gives way to the list". The first half
    asks what the list says about the show's own ids; this asks what it says
    about the id the *title* guessed. Anemone (2027) is too new to be in the
    list, so nothing contradicted its title match to AniDB 8707 - but the list
    says 8707 is AniList 10516, the 2011 Anemone, and a different AniList id
    cannot be the same show. A name that two shows share is the commonest way
    two ids get confused; an id the list has already given to somebody else is
    the proof that this is one of those times.

    False when the list does not know the id, or knows nothing to compare.
    """
    owner = ids_for(session, provider, provider_id)
    if not owner:
        return False
    for name, mine in proven.items():
        theirs = owner.get(_ALIASES.get(name.lower(), name))
        if theirs is not None and theirs != str(mine):
            return True
    return False


def drop_contradicted_title_links() -> int:
    """Detach title-guessed AniDB ids the list shows belong to another show.

    The links made before the check above existed. Only ``TITLE`` evidence is
    touched - a guess by definition, never a proven link and never the
    canonical id a Work was minted with. Returns how many were removed.
    """
    removed = 0
    with calendar_store.sessions()() as session:
        guesses = session.scalars(
            select(CalendarIdentity).where(
                CalendarIdentity.provider == "anidb", CalendarIdentity.evidence == EVIDENCE_TITLE
            )
        ).all()
        for guess in guesses:
            proven = {
                row.provider: row.provider_id
                for row in session.scalars(
                    select(CalendarIdentity).where(
                        CalendarIdentity.work_id == guess.work_id, CalendarIdentity.evidence.in_(sorted(PROVEN))
                    )
                )
            }
            if proven and owned_by_another(session, "anidb", guess.provider_id, proven):
                logger.info(
                    "dropping the title-guessed AniDB id %s from %s: the id list gives it to another show",
                    guess.provider_id, guess.work_id,
                )
                session.delete(guess)
                removed += 1
        session.commit()
    return removed


def expand_claims(session: Session, claims: list) -> list:
    """These claims, plus every id the list adds to the proven ones.

    A title-matched AniDB claim is dropped when the list names a different
    AniDB id: the list is evidence, the title match was a guess. It is also
    dropped when the list gives *that* id to a different show.
    """
    from backend.app.services.calendar_identity import Claim

    proven = {claim.normalized().provider: claim.provider_id for claim in claims if claim.evidence in PROVEN}
    if proven:
        claims = [
            claim for claim in claims
            if claim.evidence in PROVEN
            or not owned_by_another(session, claim.normalized().provider, claim.provider_id, proven)
        ]
    extra = ids_for_any(session, proven) if proven else {}
    if not extra:
        return claims
    kept = [
        claim for claim in claims
        if claim.evidence in PROVEN or extra.get(claim.normalized().provider, claim.provider_id) == claim.provider_id
    ]
    held = {claim.normalized().provider for claim in kept}
    for provider, provider_id in extra.items():
        if provider not in held:
            kept.append(Claim(provider, provider_id, evidence=EVIDENCE_MAPPING, evidence_from="id-map"))
    return kept


def ids_for_anime(known: dict[str, str]) -> dict[str, str]:
    """For the watchlist: every id reachable from an entry's own. Never raises."""
    if not known or not calendar_store.exists():
        return {}
    try:
        with calendar_store.sessions()() as session:
            return ids_for_any(session, {k: str(v) for k, v in known.items() if v and _ALIASES.get(k.lower())})
    except Exception as exc:  # noqa: BLE001 - a lookup must never break a caller
        logger.debug("id-mapping lookup failed: %s", exc)
        return {}


__all__ = [
    "MAPPING_URL", "MappingStatus", "expand_claims", "ids_for", "ids_for_any", "ids_for_anime",
    "link_all_works", "load", "parse", "refresh", "status",
]

"""One provider's answer for one season, applied all at once (build 142).

The reliability model the whole rewrite is for. Two builds regressed this in
v1 in two different ways - one cleared the stored season before fetching and
then failed, one cached an empty answer over a good one - and both produced
the same symptom: a calendar that went blank because a website was down.

So nothing here touches the database until the whole answer has been fetched,
normalized, resolved to identities and checked. Then it is applied in one
transaction, or not at all.

The other half is knowing what a *complete* answer looks like, because only a
complete one may take something away:

* an error, a timeout or a refusal - apply nothing;
* an empty season - add nothing, remove nothing;
* a season that has suddenly lost most of its entries - apply what arrived,
  remove nothing.

A provider may only ever add, keep, move or deactivate **its own**
memberships. It can never remove another provider's, and it can never remove a
Work.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.models.calendar import (
    EVIDENCE_CROSS,
    EVIDENCE_SELF,
    EVIDENCE_TITLE,
    TITLE_ENGLISH,
    TITLE_MAIN,
    TITLE_NATIVE,
    TITLE_ROMAJI,
    TITLE_SYNONYM,
    CalendarArtwork,
    CalendarGenre,
    CalendarMembership,
    CalendarSeason,
    CalendarSourceState,
    CalendarStudio,
    CalendarTitle,
    CalendarWork,
    CalendarWorkGenre,
    CalendarWorkStudio,
)
from backend.app.services import calendar_metadata, calendar_trace
from backend.app.services.calendar_identity import Claim, ensure, names_one_show
from backend.app.db.types import now_utc

logger = for_component("Calendar")

#: Below this fraction of what the provider listed last time, a snapshot is
#: treated as incomplete however cleanly it arrived.
#:
#: A provider that answers 200 with half a season has still failed; it just
#: has not said so. The number is deliberately generous - seasons do shrink as
#: listings are corrected - and it only ever withholds the *right* to
#: deactivate. Everything that did arrive is still applied.
SHRINK_FLOOR = 0.5

#: Genres the adult filter covers beyond a provider's own flag.
#:
#: The same three the interface has used since build 138. Here as well, so the
#: flag is a property of the genre row rather than a list the frontend keeps
#: privately - the filter and the "N hidden" count must read one answer.
ADULT_GENRES = frozenset({"ecchi", "hentai", "erotica"})


@dataclass
class Snapshot:
    """What one provider said about one season."""

    provider: str
    year: int
    season: str
    entries: list[AnimeCandidate] = field(default_factory=list)
    #: False when the caller already knows this is partial - a page that
    #: failed halfway, a request that was cut short.
    complete: bool = True
    #: Set when the provider could not be asked at all.
    error: str | None = None
    #: Set when the provider was deliberately not asked - no credential, not
    #: enabled. Distinct from ``error`` because it is a configuration, not a
    #: failure: it is not counted against the source and not coloured as one.
    skipped: str | None = None


@dataclass
class Applied:
    """What a snapshot did. Every number is a thing that happened."""

    provider: str = ""
    year: int = 0
    season: str = ""
    added: int = 0
    kept: int = 0
    moved: int = 0
    deactivated: int = 0
    works_created: int = 0
    conflicts: int = 0
    #: None when the snapshot was applied. A sentence when it was not.
    refused: str | None = None
    #: True when it was not applied because the source was never asked. A
    #: configuration, not a failure.
    skipped: bool = False
    #: How many entries the AniDB title index recognised. The number that says
    #: whether the dump is doing its job - it read zero for every season
    #: between builds 143 and 145, because nothing was asking it.
    anidb_matched: int = 0
    #: Set when the snapshot was applied but was not trusted to remove
    #: anything - the distinction that keeps a half-answer from emptying a
    #: season.
    incomplete_reason: str | None = None


def _norm(value: str) -> str:
    """Fold a title for matching, without losing scripts that have no latin.

    ``normalize_title_for_match`` keeps ``[a-z0-9]`` and therefore answers
    empty for a Japanese title, which would make every Japanese title match
    every other. A casefolded strip is a worse normalizer and an honest one.
    """
    from backend.app.services.release_parser import normalize_title_for_match

    folded = normalize_title_for_match(value)
    if folded:
        return folded
    return "".join(value.split()).casefold()


def validate(session: Session, snapshot: Snapshot) -> tuple[bool, str | None]:
    """May this be applied, and may it be trusted to remove things?

    Returns ``(usable, incomplete_reason)``. ``usable`` false means the
    database is not touched at all; an ``incomplete_reason`` means what
    arrived is applied and nothing is deactivated.
    """
    if snapshot.skipped:
        return False, snapshot.skipped
    if snapshot.error:
        return False, snapshot.error
    if not snapshot.complete:
        return True, "the provider's answer was already known to be partial"
    if not snapshot.entries:
        # Not an error and not a fact either. A season with nothing in it is
        # indistinguishable from a season that could not be read.
        return True, "the provider returned no entries"

    season = _season_row(session, snapshot.year, snapshot.season, create=False)
    if season is not None:
        before = session.scalar(
            select(CalendarMembership).where(
                CalendarMembership.season_id == season.id,
                CalendarMembership.provider == snapshot.provider,
                CalendarMembership.active.is_(True),
            )
        )
        if before is not None:
            previous = len(
                list(
                    session.scalars(
                        select(CalendarMembership).where(
                            CalendarMembership.season_id == season.id,
                            CalendarMembership.provider == snapshot.provider,
                            CalendarMembership.active.is_(True),
                        )
                    )
                )
            )
            if previous and len(snapshot.entries) < previous * SHRINK_FLOOR:
                return True, (
                    f"the provider listed {len(snapshot.entries)} entries where it "
                    f"previously listed {previous}"
                )
    return True, None


def _season_row(
    session: Session, year: int, season: str, *, create: bool = True
) -> CalendarSeason | None:
    row = session.scalar(
        select(CalendarSeason).where(
            CalendarSeason.year == year, CalendarSeason.season == season
        )
    )
    if row is None and create:
        row = CalendarSeason(year=year, season=season)
        session.add(row)
        session.flush()
    return row


def _titles_of(candidate: AnimeCandidate) -> tuple[str | None, ...]:
    """Every name this entry goes by, for the AniDB index to vote on."""
    return (
        candidate.display_name,
        candidate.title_romaji,
        candidate.title_english,
        candidate.title_native,
        *(candidate.aliases or ()),
    )


def _anidb_ids(entries: list[AnimeCandidate]) -> list[int | None]:
    """Each entry's AniDB id, from the published title dump.

    One pass over the index for the whole season, never one query per entry:
    two hundred separate lookups were most of what made a v1 refresh take a
    minute and a half.

    Restored in build 145 after being missed in the v2 engine, which is why
    every entry on the calendar said "No AniDB id" - nothing was asking. The
    dump is the only complete list of "this show, by every name it goes by,
    with one id", and without it Shoko cannot recognise a series the watchlist
    already has under another name.
    """
    from backend.app.services import anidb_titles

    if not entries:
        return []
    try:
        return anidb_titles.resolve_many([_titles_of(item) for item in entries])
    except Exception:  # noqa: BLE001 - an index problem must not fail a season
        logger.warning("the AniDB index could not be consulted", exc_info=True)
        return [None] * len(entries)


def _claims(candidate: AnimeCandidate, anidb_id: int | None = None) -> list[Claim]:
    """The provider's own id, plus every cross-reference it published.

    A cross-reference is proven evidence: the provider is asserting that this
    entry is the show with that id elsewhere. That assertion is exactly what
    lets five sources add up to one Work without anybody comparing titles.

    An AniDB id from the dump is deliberately *not* proven. The dump maps
    names to ids, so a match through it is a title match however many names
    voted for it - which means it is worth having and attaching, and may not
    mint a canonical id or merge two Works. That distinction is the whole
    reason ``EVIDENCE_TITLE`` exists.
    """
    claims = [Claim(candidate.provider, candidate.provider_id, evidence=EVIDENCE_SELF)]
    for provider, provider_id in (candidate.provider_ids or {}).items():
        # Only ids that name one show (build 155) - see SHOW_IDS.
        if not names_one_show(provider, provider_id):
            continue
        claims.append(
            Claim(
                provider,
                str(provider_id),
                evidence=EVIDENCE_CROSS,
                evidence_from=candidate.provider,
            )
        )
    if anidb_id is not None and not any(claim.provider == "anidb" for claim in claims):
        claims.append(
            Claim("anidb", str(anidb_id), evidence=EVIDENCE_TITLE, evidence_from="anidb-dump")
        )
    return claims


def _titles(session: Session, work_id: str, candidate: AnimeCandidate) -> None:
    wanted: list[tuple[str, str | None, str]] = []
    if candidate.display_name:
        wanted.append((TITLE_MAIN, None, candidate.display_name))
    if candidate.title_english:
        wanted.append((TITLE_ENGLISH, "en", candidate.title_english))
    if candidate.title_romaji:
        wanted.append((TITLE_ROMAJI, None, candidate.title_romaji))
    if candidate.title_native:
        wanted.append((TITLE_NATIVE, "ja", candidate.title_native))
    for alias in candidate.aliases or ():
        if alias:
            wanted.append((TITLE_SYNONYM, None, alias))

    seen: set[tuple[str, str | None]] = set()
    for kind, language, title in wanted:
        # One row per (work, provider, kind, language) - a provider with three
        # synonyms would otherwise collide with itself on the constraint.
        slot = (kind, language)
        while slot in seen:
            language = f"{language or 'x'}+"
            slot = (kind, language)
        seen.add(slot)
        existing = session.scalar(
            select(CalendarTitle).where(
                CalendarTitle.work_id == work_id,
                CalendarTitle.provider == candidate.provider,
                CalendarTitle.kind == kind,
                CalendarTitle.language.is_(language) if language is None else CalendarTitle.language == language,
            )
        )
        if existing is None:
            session.add(
                CalendarTitle(
                    work_id=work_id,
                    provider=candidate.provider,
                    kind=kind,
                    language=language,
                    title=title[:400],
                    title_norm=_norm(title)[:400],
                )
            )
        else:
            existing.title = title[:400]
            existing.title_norm = _norm(title)[:400]


def _genres(session: Session, work_id: str, candidate: AnimeCandidate) -> None:
    for raw in candidate.genres or ():
        name = (raw or "").strip()
        if not name:
            continue
        norm = name.casefold()
        genre = session.scalar(select(CalendarGenre).where(CalendarGenre.name_norm == norm))
        if genre is None:
            genre = CalendarGenre(name=name[:80], name_norm=norm[:80], adult=norm in ADULT_GENRES)
            session.add(genre)
            session.flush()
        link = session.scalar(
            select(CalendarWorkGenre).where(
                CalendarWorkGenre.work_id == work_id, CalendarWorkGenre.genre_id == genre.id
            )
        )
        if link is None:
            session.add(CalendarWorkGenre(work_id=work_id, genre_id=genre.id))


def _studios(session: Session, work_id: str, candidate: AnimeCandidate) -> None:
    for raw in candidate.studios or ():
        name = (raw or "").strip()
        if not name:
            continue
        norm = name.casefold()
        studio = session.scalar(select(CalendarStudio).where(CalendarStudio.name_norm == norm))
        if studio is None:
            studio = CalendarStudio(name=name[:200], name_norm=norm[:200])
            session.add(studio)
            session.flush()
        link = session.scalar(
            select(CalendarWorkStudio).where(
                CalendarWorkStudio.work_id == work_id,
                CalendarWorkStudio.studio_id == studio.id,
            )
        )
        if link is None:
            session.add(CalendarWorkStudio(work_id=work_id, studio_id=studio.id))


def _artwork(session: Session, work_id: str, candidate: AnimeCandidate) -> None:
    """Choose one poster per Work, and stop.

    v1 downloaded every source's poster so they could be compared. The
    calendar only ever draws one, so this picks the first provider in order
    that offers something that is not obviously a placeholder, and the others
    are never fetched.

    Only the choice is made here. The bytes are fetched afterwards, outside
    the transaction, because a poster download has no business holding a
    database lock.
    """
    from backend.app.services.calendar_providers import is_placeholder_url

    url = candidate.cover_url
    if not url or is_placeholder_url(url):
        return
    existing = session.get(CalendarArtwork, work_id)
    if existing is None:
        session.add(
            CalendarArtwork(work_id=work_id, provider=candidate.provider, source_url=url)
        )
        return
    # A better-placed provider may take over a choice that has not been
    # fetched yet; one already on disk is left alone.
    if existing.file is None and calendar_metadata._rank(
        candidate.provider
    ) < calendar_metadata._rank(existing.provider):
        existing.provider = candidate.provider
        existing.source_url = url


def apply_snapshot(session: Session, snapshot: Snapshot) -> Applied:
    """Resolve, validate and apply one provider's season, or nothing at all.

    The transaction boundary is the caller's - this runs inside one session
    and commits at the end, or raises and leaves the session for the caller to
    roll back. That is what makes a half-applied season impossible.
    """
    result = Applied(provider=snapshot.provider, year=snapshot.year, season=snapshot.season)
    usable, incomplete = validate(session, snapshot)
    if not usable:
        result.refused = incomplete
        result.skipped = bool(snapshot.skipped)
        if snapshot.skipped:
            # Recorded so the page can say "not configured", and deliberately
            # not counted: a source nobody set up has not failed at anything.
            _note_skipped(session, snapshot.provider, snapshot.skipped)
            logger.debug(
                "%s not asked for %s %s: %s",
                snapshot.provider,
                snapshot.season,
                snapshot.year,
                snapshot.skipped,
            )
            calendar_trace.record(
                "snapshot",
                f"{snapshot.provider} not asked for {snapshot.season} {snapshot.year}",
                reason=snapshot.skipped,
            )
            return result
        _record_source(session, snapshot.provider, ok=False, error=incomplete)
        logger.warning(
            "%s %s %s: snapshot refused (%s)",
            snapshot.provider,
            snapshot.season,
            snapshot.year,
            incomplete,
        )
        calendar_trace.record(
            "snapshot",
            f"{snapshot.provider} refused for {snapshot.season} {snapshot.year}",
            level="warn",
            reason=incomplete,
        )
        return result
    result.incomplete_reason = incomplete

    season = _season_row(session, snapshot.year, snapshot.season)
    assert season is not None
    season.last_attempt_at = now_utc()

    seen_ids: set[str] = set()
    touched: set[str] = set()

    # Resolved for the whole season before the loop, for the same reason v1
    # did it that way: one pass over the index instead of one query per show.
    anidb_ids = _anidb_ids(snapshot.entries)
    matched = 0

    for index, candidate in enumerate(snapshot.entries):
        if not str(candidate.provider_id or "").strip():
            continue
        before = session.scalar(
            select(CalendarMembership).where(
                CalendarMembership.season_id == season.id,
                CalendarMembership.provider == snapshot.provider,
                CalendarMembership.provider_id == str(candidate.provider_id),
            )
        )
        aid = anidb_ids[index] if index < len(anidb_ids) else None
        if aid is not None:
            matched += 1
        # Every id the offline list knows is claimed with it (build 154), so
        # the Work arrives already linked to AniDB, AniList, MAL and Kitsu.
        from backend.app.services.id_mapping import expand_claims

        work = ensure(session, expand_claims(session, _claims(candidate, aid)), label=candidate.display_name or "")
        if work.id not in touched:
            touched.add(work.id)
        seen_ids.add(str(candidate.provider_id))

        moved = _move_from_other_seasons(session, snapshot, season.id, candidate)
        if moved:
            result.moved += 1

        if before is None:
            session.add(
                CalendarMembership(
                    season_id=season.id,
                    work_id=work.id,
                    provider=snapshot.provider,
                    provider_id=str(candidate.provider_id),
                    active=True,
                )
            )
            result.added += 1
        else:
            before.last_seen = now_utc()
            before.work_id = work.id
            before.active = True
            result.kept += 1

        calendar_metadata.observe(session, work.id, candidate)
        _titles(session, work.id, candidate)
        _genres(session, work.id, candidate)
        _studios(session, work.id, candidate)
        _artwork(session, work.id, candidate)

    if incomplete is None:
        result.deactivated = _deactivate_unseen(session, snapshot, season.id, seen_ids)
    elif snapshot.entries:
        logger.info(
            "%s %s %s: nothing deactivated - %s",
            snapshot.provider,
            snapshot.season,
            snapshot.year,
            incomplete,
        )

    for work_id in touched:
        work = session.get(CalendarWork, work_id)
        if work is None:
            continue
        calendar_metadata.assemble(session, work)
        result.conflicts += len(calendar_metadata.conflicts(session, work_id))

    calendar_metadata.score_popularity(session)

    if incomplete is None:
        season.last_success_at = now_utc()
    _record_source(session, snapshot.provider, ok=True, error=None)

    logger.info(
        "%s %s %s: +%s added, %s kept, %s moved, %s deactivated",
        snapshot.provider,
        snapshot.season,
        snapshot.year,
        result.added,
        result.kept,
        result.moved,
        result.deactivated,
    )
    result.anidb_matched = matched
    calendar_trace.record(
        "snapshot",
        f"{snapshot.provider} applied for {snapshot.season} {snapshot.year}",
        level="warn" if result.incomplete_reason else "info",
        entries=len(snapshot.entries),
        added=result.added,
        kept=result.kept,
        moved=result.moved,
        deactivated=result.deactivated,
        conflicts=result.conflicts,
        anidb=result.anidb_matched,
        held_back=result.incomplete_reason,
    )
    return result


def _move_from_other_seasons(
    session: Session, snapshot: Snapshot, season_id: int, candidate: AnimeCandidate
) -> bool:
    """This provider has changed its mind about which season this belongs to.

    Its old membership is deactivated rather than deleted: that the provider
    once said FALL is a fact, and the disagreement between providers about
    which season a show belongs to is information the calendar keeps on
    purpose.
    """
    moved = False
    for row in session.scalars(
        select(CalendarMembership).where(
            CalendarMembership.provider == snapshot.provider,
            CalendarMembership.provider_id == str(candidate.provider_id),
            CalendarMembership.season_id != season_id,
            CalendarMembership.active.is_(True),
        )
    ):
        row.active = False
        moved = True
    return moved


def _deactivate_unseen(
    session: Session, snapshot: Snapshot, season_id: int, seen_ids: set[str]
) -> int:
    """Only this provider's rows, and only after a complete answer."""
    count = 0
    for row in session.scalars(
        select(CalendarMembership).where(
            CalendarMembership.season_id == season_id,
            CalendarMembership.provider == snapshot.provider,
            CalendarMembership.active.is_(True),
        )
    ):
        if row.provider_id not in seen_ids:
            row.active = False
            count += 1
    return count


def _note_skipped(session: Session, provider: str, reason: str) -> None:
    """Remember that a source was not asked, without blaming it.

    The failure count is left alone and the attempt time is not moved: the
    debug page reads "not configured" from the error text and shows no
    failures, which is the true shape of a source nobody set up.
    """
    row = session.scalar(
        select(CalendarSourceState).where(CalendarSourceState.provider == provider)
    )
    if row is None:
        row = CalendarSourceState(provider=provider, failures=0)
        session.add(row)
    row.last_error = reason[:400]


def _record_source(session: Session, provider: str, *, ok: bool, error: str | None) -> None:
    row = session.scalar(
        select(CalendarSourceState).where(CalendarSourceState.provider == provider)
    )
    if row is None:
        # failures=0 explicitly: a column default is applied at flush, and
        # this row is incremented before it ever gets there.
        row = CalendarSourceState(provider=provider, failures=0)
        session.add(row)
    row.last_attempt_at = now_utc()
    if ok:
        row.last_success_at = now_utc()
        row.failures = 0
        row.last_error = None
    else:
        row.failures += 1
        row.last_error = (error or "")[:400]


def apply_one(session: Session, candidate: AnimeCandidate) -> CalendarWork:
    """One show, as a provider described it, written into the store (build 154).

    The season update's per-show half, without the season: no membership is
    made, so the show appears in lookups and fills the watchlist without
    appearing on any season page it was never listed in. It is how an entry
    the calendar has never seen - an old series adopted from Shoko Server -
    gets into the one store instead of into a private copy on the watchlist.

    The caller commits.
    """
    from backend.app.services.id_mapping import expand_claims

    work = ensure(session, expand_claims(session, _claims(candidate)), label=candidate.display_name or "")
    calendar_metadata.observe(session, work.id, candidate)
    _titles(session, work.id, candidate)
    _genres(session, work.id, candidate)
    _studios(session, work.id, candidate)
    _artwork(session, work.id, candidate)
    calendar_metadata.assemble(session, work)
    return work


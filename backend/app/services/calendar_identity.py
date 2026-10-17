"""Who a Calendar Work is, and the id it keeps for ever (build 141).

The one rule everything here serves: **a canonical id is minted once and never
changes.** A better identity discovered later attaches to the Work; it does
not rename it.

That is a change from how v1 thought about identity, and the reason is the
television client. A public id is a contract: something holding ``adb2342`` is
broken by a renamed Work exactly as badly as by a deleted database. The second
reason is measured rather than theoretical - when identity was last rebuilt
(build 83), AniDB had never heard of four entries in ten for the *upcoming*
season, so an id that improved itself would churn hardest on the page people
actually look at.

AniDB still has the highest priority. It simply has to be *proven* before it
counts, and the published dump - which carries titles and nothing else -
cannot prove anything on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.calendar import (
    PROVEN,
    CalendarAlias,
    CalendarIdentity,
    CalendarWork,
    EVIDENCE_SELF,
)

logger = for_component("Calendar")

#: Three names for one set of ids.
#:
#: Jikan is an unofficial MyAnimeList API: its ``provider_id`` is the MAL id
#: (``mal_id`` in its own payload, which it also republishes as
#: ``provider_ids["myanimelist"]``). ``mal`` and ``myanimelist`` are the two
#: spellings already in the codebase - v1 carried a special case for that pair
#: in the linking pass.
#:
#: Without this, a show described by AniList, MAL and Jikan becomes three
#: Works with three ids, and nothing ever notices.
NAMESPACE = {
    "jikan": "mal",
    "myanimelist": "mal",
}

#: Prefix per provider. **Permanent.**
#:
#: A prefix is part of every id ever handed out, so it can never be reassigned
#: or re-spelled. A future provider that would collide gets a new prefix; it
#: does not get to take one of these.
PREFIXES = {
    "anidb": "adb",
    "anilist": "al",
    "mal": "mal",
    "kitsu": "kit",
    "shikimori": "shi",
    "simkl": "smk",
    "animeschedule": "asc",
    "tmdb": "tmdb",
    "annict": "anc",
    "livechart": "lc",
    "thetvdb": "tvdb",
}

#: Which identity mints the id when several are proven at once.
#:
#: An identity priority, not a metadata priority: AniDB anchors the id because
#: it is the identity the wider AniFlow and Shoko ecosystem runs on, while
#: most of the metadata on the same Work will come from AniList.
PRIORITY = ("anidb", "anilist", "mal", "kitsu", "shikimori")


@dataclass(frozen=True)
class Claim:
    """One provider id, and how it came to be attached to this Work."""

    provider: str
    provider_id: str
    evidence: str = EVIDENCE_SELF
    evidence_from: str | None = None

    def normalized(self) -> Claim:
        provider = canonical_provider(self.provider)
        return Claim(provider, str(self.provider_id), self.evidence, self.evidence_from)

    @property
    def proven(self) -> bool:
        return self.evidence in PROVEN


#: The ids that name exactly one show (build 155).
#:
#: Kitsu also publishes TVDB and TMDB ids, and those name a *series* - every
#: season of a show shares one. Treated as identities they were three bugs at
#: once: a new season's listing attached to whichever show already held the
#: series id, the watchlist copied them onto its entries, and "In your
#: library" matched half of an upcoming season against one entry that carried
#: a shared one. They are still stored as metadata; they are never an identity.
SHOW_IDS = frozenset({"anidb", "anilist", "mal", "myanimelist", "jikan", "kitsu", "shikimori"})


def names_one_show(provider: str, provider_id: object) -> bool:
    """Whether this id can say which show something is."""
    value = str(provider_id or "").strip()
    return (provider or "").strip().lower() in SHOW_IDS and bool(value) and value != "0"


def canonical_provider(provider: str) -> str:
    """``jikan`` and ``myanimelist`` are both ``mal``."""
    key = (provider or "").strip().lower()
    return NAMESPACE.get(key, key)


def mint(provider: str, provider_id: str) -> str:
    """``anilist`` + ``54`` becomes ``al54``.

    Deterministic, so the same identity always produces the same id - which is
    what lets a rebuild verify the ids it holds rather than invent new ones.
    """
    key = canonical_provider(provider)
    prefix = PREFIXES.get(key)
    if not prefix:
        # Better a long ugly id than a collision with a real prefix. A
        # provider worth keeping gets an entry above.
        logger.warning("no id prefix registered for provider %r", key)
        prefix = f"x{key[:6]}"
    return f"{prefix}{str(provider_id).strip()}"


def by_identity(session: Session, provider: str, provider_id: str) -> CalendarWork | None:
    """The Work holding this provider id, if one does."""
    row = session.scalar(
        select(CalendarIdentity).where(
            CalendarIdentity.provider == canonical_provider(provider),
            CalendarIdentity.provider_id == str(provider_id),
        )
    )
    return session.get(CalendarWork, row.work_id) if row else None


def resolve(session: Session, public_id: str) -> CalendarWork | None:
    """A Work by its public id, following a merge if one has happened.

    The path a television client depends on: an id it stored a year ago must
    still answer, whether or not the Work has since been merged into another.
    """
    wanted = (public_id or "").strip()
    if not wanted:
        return None
    work = session.get(CalendarWork, wanted)
    if work is not None:
        return work
    alias = session.get(CalendarAlias, wanted)
    return session.get(CalendarWork, alias.work_id) if alias else None


def _existing(session: Session, claims: list[Claim]) -> list[CalendarWork]:
    """Every Work any of these ids already belongs to, without duplicates."""
    found: dict[str, CalendarWork] = {}
    for claim in claims:
        work = by_identity(session, claim.provider, claim.provider_id)
        if work is not None:
            found[work.id] = work
    return list(found.values())


def attach(session: Session, work: CalendarWork, claim: Claim) -> bool:
    """Record a provider id against a Work. True when it was new.

    A provider id belongs to exactly one Work, so a claim already held by a
    *different* Work is refused here rather than silently moved - moving it
    would be a merge, and merges are a decision with their own rules.
    """
    claim = claim.normalized()
    existing = session.scalar(
        select(CalendarIdentity).where(
            CalendarIdentity.provider == claim.provider,
            CalendarIdentity.provider_id == claim.provider_id,
        )
    )
    if existing is not None:
        if existing.work_id != work.id:
            logger.warning(
                "%s:%s is held by %s, not %s; left where it is",
                claim.provider,
                claim.provider_id,
                existing.work_id,
                work.id,
            )
            return False
        # Evidence can only get stronger. A title match arriving later must
        # not downgrade a link a person confirmed.
        if existing.evidence not in PROVEN and claim.proven:
            existing.evidence = claim.evidence
            existing.evidence_from = claim.evidence_from
        return False
    session.add(
        CalendarIdentity(
            work_id=work.id,
            provider=claim.provider,
            provider_id=claim.provider_id,
            evidence=claim.evidence,
            evidence_from=claim.evidence_from,
        )
    )
    return True


def ensure(session: Session, claims: list[Claim], *, label: str = "") -> CalendarWork:
    """The Work these ids describe, creating it if this is the first sight.

    Three outcomes:

    * **Known.** One of the ids is already held - that Work is returned and
      any new ids are attached to it. This is the merge the design wants and
      it costs nothing: a provider publishing a cross-reference to an id we
      already have *is* the evidence.
    * **New.** None is held - a Work is created, and its id is minted from the
      highest-priority *proven* claim.
    * **Ambiguous.** Two ids are held by two different Works. Nothing is
      merged. See below.

    The ambiguous case is left alone on purpose. Merging two Works is the one
    operation in here that cannot be undone by downloading something again,
    and a provider that has mixed up two shows would otherwise make AniFlow
    mix them up permanently. They stay separate and can be reconciled later.
    """
    normalized = [claim.normalized() for claim in claims if str(claim.provider_id).strip()]
    if not normalized:
        raise ValueError("a Work needs at least one provider id")

    known = _existing(session, normalized)
    if len(known) > 1:
        logger.warning(
            "%s ids point at %s different works (%s); leaving them separate",
            len(normalized),
            len(known),
            ", ".join(sorted(work.id for work in known)),
        )
    if known:
        work = _preferred(known)
        for claim in normalized:
            attach(session, work, claim)
        if label and not work.label:
            work.label = label[:400]
        return work

    minted = _minting_claim(normalized)
    work = CalendarWork(
        id=mint(minted.provider, minted.provider_id),
        id_provider=minted.provider,
        id_provider_id=minted.provider_id,
        label=label[:400],
    )
    session.add(work)
    session.flush()
    for claim in normalized:
        attach(session, work, claim)
    logger.debug("new calendar work %s (%s)", work.id, label or minted.provider)
    return work


def _minting_claim(claims: list[Claim]) -> Claim:
    """The claim a new Work takes its id from.

    Highest priority wins, but only among *proven* claims: a title match may
    never mint an id, least of all an AniDB one. If nothing is proven - which
    should not happen, since the provider describing an entry is proof of its
    own id - the first claim is used rather than failing, because a Work with
    an awkward id is recoverable and a season that refused to load is not.
    """
    proven = [claim for claim in claims if claim.proven] or claims
    return min(proven, key=lambda claim: _rank(claim.provider))


def _preferred(works: list[CalendarWork]) -> CalendarWork:
    """When several Works matched, the one to hang new ids on.

    The oldest, by id-minting priority then by creation. Deterministic so that
    the same inputs give the same answer twice.
    """
    return min(works, key=lambda work: (_rank(work.id_provider), work.created_at, work.id))


def _rank(provider: str) -> int:
    key = canonical_provider(provider)
    return PRIORITY.index(key) if key in PRIORITY else len(PRIORITY)


def merge(session: Session, *, keep: CalendarWork, absorb: CalendarWork, evidence: str) -> bool:
    """Fold one Work into another, leaving the absorbed id resolvable for ever.

    Only on proven evidence. This is the operation the design is most careful
    about: two shows merged by mistake cannot be separated again by any amount
    of re-downloading, because the thing that told them apart is gone.

    The absorbed id becomes an alias rather than disappearing - that is the
    entire reason aliases exist, and why a client may hold an id indefinitely.
    """
    if evidence not in PROVEN:
        logger.warning(
            "refusing to merge %s into %s on %s evidence", absorb.id, keep.id, evidence
        )
        return False
    if keep.id == absorb.id:
        return False

    for identity in session.scalars(
        select(CalendarIdentity).where(CalendarIdentity.work_id == absorb.id)
    ):
        held = session.scalar(
            select(CalendarIdentity).where(
                CalendarIdentity.provider == identity.provider,
                CalendarIdentity.provider_id == identity.provider_id,
                CalendarIdentity.work_id == keep.id,
            )
        )
        if held is None:
            identity.work_id = keep.id
        else:
            session.delete(identity)

    # Aliases already pointing at the absorbed Work follow it, or a client
    # holding a twice-merged id would find nothing.
    for alias in session.scalars(
        select(CalendarAlias).where(CalendarAlias.work_id == absorb.id)
    ):
        alias.work_id = keep.id

    session.add(CalendarAlias(alias_id=absorb.id, work_id=keep.id, evidence=evidence))
    session.delete(absorb)
    logger.info("merged calendar work %s into %s on %s", absorb.id, keep.id, evidence)
    return True

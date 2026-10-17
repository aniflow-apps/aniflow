"""One question, asked once: what does this group publish for this show?

Build 95, and it exists because the add-anime dialog and the server were
asking two different questions and showing one answer.

The dialog searched broadly - every name variant with every quality appended -
grouped what came back by group and quality, and offered a row per group. The
row said "12 episodes found". Confirming it then had the *server* search
again, with a different query (one name, plus the group and the quality as
search text), and filter the results with three rules the dialog had never
applied: the release must name a single episode, it must belong to the folder
that was picked, and the anime's profile must accept it. Every one of those
can remove everything, and when it did the episode list came up empty with no
explanation anywhere. That is the bug reported against build 94, and it is the
same shape as build 91's: two code paths, one of them shown to the user and
the other one authoritative.

So the narrow question now has exactly one implementation, and it is this
module. The dialog runs it when a row is selected - which is what draws the
episode numbers in the box - and adding the anime runs it again to write the
rows. Same query, same filters, same verdicts. If the box says twelve, twelve
is what gets stored; if the box says nothing, it says why before anything is
created.

**Every release is kept, with its verdict.** A scan that returned only what it
accepted could not answer the one question worth asking when a box comes back
empty - *these files are obviously there, so why is none of them mine?* The
reasons are the checks in the order they run, so the first one that fires is
the honest answer.

**One request per second.** The pages of one scan go to the same tracker, and
a handful of requests arriving together is what a rate limiter exists to
stop - see INDEXER_PAUSE_SECONDS. The preview fan-out paces itself in the
browser; this paces itself here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.integrations.search.base import (
    SearchError,
    SearchProvider,
    SearchRequest,
    SearchResult,
)
from backend.app.models.anime import Anime
from backend.app.services.release_identity import (
    release_matches_anime,
    release_matches_reference,
)
from backend.app.services.release_parser import ParsedRelease, parse_release_name

logger = for_component("Search")

#: Why a release is not one of this selection's episodes. Stable keys rather
#: than sentences: the dialog translates them, and a test can assert on them.
#:
#: The order is the order the checks run in, which is also the order of
#: increasing specificity - "this is not the show" before "the show's profile
#: refuses it".
UNREADABLE = "unreadable"
NO_TITLE = "no-title"
NO_EPISODE = "no-episode"
OTHER_SHOW = "other-show"
PROFILE = "profile"


@dataclass
class ScannedRelease:
    """One search hit, and whether it is an episode of the selection."""

    id: str
    raw_name: str
    #: Everything the parser recognised, for the More info window. None when
    #: the name could not be read at all, which is a finding in itself.
    subgroup: str | None = None
    resolution: str | None = None
    season: int | None = None
    #: As written in the name: "01", or "01-12" for a batch.
    episode: str | None = None
    #: Everything the name says about itself that no other field accounted
    #: for - "uncensored", "dual-audio", the platform, and any label nobody
    #: has invented yet. See services/release_edition.py.
    #:
    #: Called tags, not edition: an *edition* is the subset that actually
    #: distinguishes the copies, which needs every file of a group to decide.
    tags: tuple[str, ...] = ()
    #: The episode this release *is*, or None for a batch, a range, or a
    #: special whose number is not a plain integer.
    number: int | None = None
    version: int = 1
    crc32: str | None = None
    folder_name: str | None = None
    size: int | None = None
    seeders: int | None = None
    #: Where to get it. Kept out of the API response on purpose - the More
    #: info window is about names and verdicts, and a magnet for an anime
    #: nobody has added yet is not something to hand the browser.
    magnet_url: str | None = None
    torrent_url: str | None = None
    accepted: bool = False
    #: No episode in the name, and the title is the selected show: a whole season in one torrent.
    whole_season: bool = False
    #: One of the keys above, or None when accepted.
    reason: str | None = None
    #: What the profile objected to, when the reason is PROFILE. The
    #: evaluator's own words, which name the rule rather than the outcome.
    detail: list[str] = field(default_factory=list)
    #: What the profile asks for and the name does not say, on a release that
    #: was accepted all the same: ``["resolution_unknown"]`` (build 220). The
    #: person sees it beside the file and decides.
    warnings: list[str] = field(default_factory=list)


@dataclass
class QueryOutcome:
    """One name that was searched, and what asking under it achieved.

    ``found`` counts what came back, ``new`` counts what no earlier name had
    already turned up. A name with ``new`` above zero is earning its request -
    and a name with ``found`` at zero while another name found twelve episodes
    is the Makina-san bug, named.
    """

    term: str
    found: int = 0
    new: int = 0
    error: str | None = None


@dataclass
class SelectionScan:
    """What one group at one quality has for one show."""

    #: Every name that was searched. More than one because which spelling a
    #: group used is a coin toss - see the module docstring.
    queries: list[str]
    subgroup: str | None
    resolution: str | None
    #: The release name the selection is matched against - the folder picked
    #: in the dialog, or the anime's stored release name.
    reference: str
    #: The edition that was picked, if any. Empty means any edition.
    edition: tuple[str, ...] = ()
    #: What the other copies are recognised by, which this one is not.
    edition_not: tuple[str, ...] = ()
    releases: list[ScannedRelease] = field(default_factory=list)
    #: Every hit exactly as the tracker gave it, including the links. Kept so
    #: adding can write these down instead of searching again - see
    #: services/scan_store.py - and never sent to the browser.
    hits: list[object] = field(default_factory=list)
    #: Accepted episode numbers, sorted, without duplicates.
    episodes: list[int] = field(default_factory=list)
    pages: int = 0
    #: One entry per name searched, in the order they were asked.
    asked: list[QueryOutcome] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    #: False when the indexer could not be reached at all, which is a
    #: different thing from an indexer that answered with nothing.
    reached_provider: bool = True
    #: True when there is no indexer to ask. Not a failure: an anime can be
    #: added without one and fed from RSS.
    no_indexer: bool = False

    @property
    def accepted(self) -> list[ScannedRelease]:
        return [release for release in self.releases if release.accepted]

    @property
    def rejected(self) -> list[ScannedRelease]:
        return [release for release in self.releases if not release.accepted]


def canonical_episode_number(number: str | None) -> int | None:
    """The single episode a label names, or None if it names no single one.

    A range ("01-12") and a batch are deliberately not episodes: they are one
    file holding many, and treating one as episode 1 was how a batch used to
    become the only thing an anime appeared to have.
    """
    stripped = (number or "").strip()
    return int(stripped) if stripped.isdigit() else None


def matches_selected_folder(
    parsed: ParsedRelease,
    anime: Anime | None,
    reference: str,
    alternates: Iterable[str | None] = (),
) -> bool:
    """Whether a parsed release belongs to the folder that was picked.

    Delegates to the shared identity rule, so a release found by searching is
    matched exactly like one arriving through a feed - one season policy, one
    set of tests. The picked folder is the benchmark; an existing anime's own
    stored name is accepted as well, because changing the release of an anime
    already in the watchlist searches under the new name while the old one is
    still what the row says.

    ``alternates`` are the show's other spellings - in practice the names the
    scan searched under (build 96). A group that publishes under the other
    title was being found and then rejected as a different show; the season
    still comes from ``reference``, so this widens which name counts and
    nothing else.
    """
    from backend.app.services.library import cleaned_release_folder

    try:
        cleaned_release_folder(parsed)  # rejects titles with no usable folder name
    except ValueError:
        return False

    if release_matches_reference(parsed, reference, alternates=alternates):
        return True
    return anime is not None and release_matches_anime(parsed, anime)


def profile_accepts(
    session: Session | None,
    anime: Anime | None,
    parsed: ParsedRelease,
    subgroup: str | None,
    resolution: str | None,
    reference: str | None = None,
    alternates: Iterable[str | None] = (),
    edition: Iterable[str] = (),
    edition_not: Iterable[str] = (),
    warnings: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Whether this release is one the profile would take, and why not.

    A name that does not state its quality, source or censoring is **not**
    refused here (build 220): most releases leave something out, and the dialog
    is where a person looks and chooses. What could not be checked is appended
    to ``warnings`` when one is passed, so it can be shown beside the file.
    A name that states something *different* is still refused.

    The same rules queue time uses, applied where the answer is first shown -
    so "available" means available *to you*, not "exists somewhere".

    The subgroup and quality passed in win over any stored profile: while an
    anime is being added the profile row does not exist yet, and those two are
    what was picked in the dialog a moment ago.

    With neither a profile nor a choice, everything is kept. That is the
    honest answer to "no rules configured".
    """
    from backend.app.services.matching import evaluate_release
    from backend.app.services.rss_scan import _candidate_titles

    profile = anime.profile if anime is not None else None
    # `None` is "no constraint"; the empty string is "a release with no group
    # in its name", which is what an Unknown-group row is asking for.
    subgroups = (
        [subgroup]
        if subgroup is not None
        else list(profile.subgroups if profile else [])
    )
    wanted_resolution = resolution or (profile.resolution if profile else None)
    wanted_edition = list(edition) if edition else list(profile.edition if profile else [])
    refused_edition = (
        list(edition_not) if edition_not else list(profile.edition_not if profile else [])
    )
    if not subgroups and not wanted_resolution and not wanted_edition and not refused_edition:
        return True, []

    result = evaluate_release(
        parsed,
        subgroups=subgroups,
        resolution=wanted_resolution,
        media_source=profile.media_source if profile else None,
        edition=wanted_edition,
        edition_not=refused_edition,
        censor_preference=(
            profile.censor_preference.value
            if profile and hasattr(profile.censor_preference, "value")
            else "ANY"
        ),
        # The folder that was picked, plus whatever the anime is called when
        # there is one. The reference is always included: while an anime is
        # being added it is the only name anything knows, and with none of
        # them `evaluate_release` finds that every release contradicts every
        # known title and refuses the whole season.
        candidate_titles=[
            name
            for name in (
                reference,
                # The show's other spellings (build 96). Without them a group
                # that publishes under the other title is identified as this
                # show by the rule above and then refused by this one, which
                # is the same bug twice.
                *alternates,
                *(_candidate_titles(anime) if anime is not None else ()),
            )
            if name
        ],
        # An absent title is tolerated: this release has already been matched
        # to the picked folder, which is the stricter test of the two.
        require_title_match=False,
        require_resolution_known=profile.require_resolution_known if profile else False,
        require_source_known=profile.require_source_known if profile else False,
        require_censor_known=profile.require_censor_known if profile else False,
        tolerate_unknown=True,
    )
    if warnings is not None:
        warnings.extend(result.warnings)
    return result.matched, list(result.failures or [])


def stored_rules(
    anime: Anime | None,
    resolution: str | None = None,
    edition: Iterable[str] = (),
    edition_not: Iterable[str] = (),
) -> list[dict[str, str]]:
    """The rules of the anime's stored profile that this scan obeys without the dialog having asked (item 48).

    ``profile_accepts`` lets what the dialog chose win and falls back to the profile for the rest, so a
    dialog showing "Any" quality can still be refusing everything that is not 480p - what made Sukebei's
    releases read as refused in build 220. Said in the dialog so the answer to "why is nothing available"
    is on the screen. The subgroup is not listed: the dialog always states it.
    """
    profile = anime.profile if anime is not None else None
    if profile is None:
        return []
    rules: list[dict[str, str]] = []
    if not resolution and profile.resolution:
        rules.append({"kind": "resolution", "value": str(profile.resolution)})
    if profile.media_source:
        rules.append({"kind": "media_source", "value": str(profile.media_source)})
    censor = getattr(profile.censor_preference, "value", profile.censor_preference)
    if censor and str(censor) != "ANY":
        rules.append({"kind": "censor", "value": str(censor).lower()})
    if not list(edition) and profile.edition:
        rules.append({"kind": "edition", "value": ", ".join(profile.edition)})
    if not list(edition_not) and profile.edition_not:
        rules.append({"kind": "edition_not", "value": ", ".join(profile.edition_not)})
    return rules


def scan_selection(
    *,
    queries: Sequence[str] | str,
    reference: str,
    subgroup: str | None = None,
    resolution: str | None = None,
    edition: Iterable[str] = (),
    edition_not: Iterable[str] = (),
    session: Session | None = None,
    anime: Anime | None = None,
    provider: SearchProvider | None = None,
    max_pages: int | None = None,
    pause_seconds: float | None = None,
    known: Sequence[SearchResult] | None = None,
    indexer_id: str | None = None,
) -> SelectionScan:
    """Ask the indexer for one group's releases of one show, and judge them.

    ``queries`` are the names to search under - all of them, because a group
    publishes under one spelling and the dialog offered another. ``reference``
    is the release name the answers are matched against, which is the folder
    that was picked; it is what keeps a second name from admitting a second
    show. ``anime`` is None while an anime is being added, which is why the
    subgroup and the quality are passed separately.
    """
    from backend.app.services import discovery

    settings = discovery._search_settings()
    pause = discovery.INDEXER_PAUSE_SECONDS if pause_seconds is None else pause_seconds
    names = [queries] if isinstance(queries, str) else list(queries)
    # Deduplicated, blanks dropped, order kept: the first name is the one the
    # user typed and it should be asked first.
    names = list(dict.fromkeys(name.strip() for name in names if name and name.strip()))
    scan = SelectionScan(
        queries=names,
        subgroup=subgroup,
        resolution=resolution,
        edition=tuple(edition),
        edition_not=tuple(edition_not),
        reference=reference,
    )
    if known is not None:
        # Judge what the caller already has, and ask the tracker nothing. The
        # verdicts are the same ones a fresh search would produce - the same
        # function decides them - so this is the identical answer without the
        # second chance to fail. See the module docstring.
        seen_names: set[str] = set()
        for result in known:
            if result.id in seen_names:
                continue
            seen_names.add(result.id)
            scan.hits.append(result)
            scan.releases.append(_judge(result, session=session, anime=anime, scan=scan))
        scan.asked = [
            QueryOutcome(term=name, found=len(scan.releases), new=len(scan.releases))
            for name in names[:1]
        ]
        scan.episodes = sorted(
            {
                release.number
                for release in scan.releases
                if release.accepted and release.number
            }
        )
        return scan
    if not names:
        return scan
    if provider is None:
        try:
            # No argument: the tests replace this function with a
            # zero-argument stand-in, and that seam is how every search path
            # in the suite avoids the network.
            #
            # The argument appears only when there is an id: the one picked in
            # the dialog, else the one this anime was added from.
            chosen = indexer_id or (anime.indexer_id if anime is not None else None)
            provider = (
                discovery._get_provider(indexer_id=chosen)
                if chosen
                else discovery._get_provider()
            )
        except discovery.NoIndexerConfigured as exc:
            # Adding an anime must still work with no indexer: the watchlist
            # entry is the point, and releases arrive from feeds too.
            logger.info("no indexer configured, nothing to scan: %s", exc)
            scan.no_indexer = True
            scan.failures.append(str(exc))
            return scan

    seen: set[str] = set()
    answered = False
    requested = 0
    for name in names:
        outcome = QueryOutcome(term=name)
        scan.asked.append(outcome)
        request = SearchRequest(anime=name, subgroup=subgroup, resolution=resolution)
        # The fullest page this name has returned. A later page holding fewer
        # is the last one, so the empty page after it is never asked for.
        page_size = 0
        for page in range(1, (max_pages or settings.search_max_pages) + 1):
            if requested and pause > 0:
                # One request at a time, a second apart. Every request of this
                # scan goes to the same tracker.
                time.sleep(pause)
            requested += 1
            try:
                results = provider.search(request, page=page, limit=discovery.RESULTS_PER_PAGE)
            except SearchError as exc:
                logger.warning("selection scan failed for %r: %s", name, exc)
                outcome.error = str(exc)
                scan.failures.append(f"{name}: {exc}")
                break
            answered = True
            scan.pages = max(scan.pages, page)
            if not results:
                break
            for result in results:
                outcome.found += 1
                if result.id in seen:
                    continue
                seen.add(result.id)
                outcome.new += 1
                scan.hits.append(result)
                scan.releases.append(_judge(result, session=session, anime=anime, scan=scan))
            if page_size and len(results) < page_size:
                break
            page_size = max(page_size, len(results))

    scan.reached_provider = answered
    scan.episodes = sorted(
        {release.number for release in scan.releases if release.accepted and release.number}
    )
    return scan


def _judge(result, *, session: Session | None, anime: Anime | None, scan: SelectionScan) -> ScannedRelease:
    """One hit, read and checked in the order the checks matter."""
    from backend.app.services.library import cleaned_release_folder

    try:
        parsed = parse_release_name(result.title)
    except ValueError as exc:
        return ScannedRelease(
            id=result.id,
            raw_name=result.title,
            size=result.size,
            seeders=result.seeders,
            reason=UNREADABLE,
            detail=[str(exc)],
        )

    # Noted while the names are in hand, so the list of what the parser reads
    # badly builds itself from real searches rather than from screenshots.
    # See services/parse_log.py - best effort, and never in the way.
    from backend.app.services import parse_log

    parse_log.record(parsed)

    try:
        folder = cleaned_release_folder(parsed)
    except ValueError:
        folder = None
    row = ScannedRelease(
        id=result.id,
        raw_name=result.title,
        subgroup=parsed.subgroup,
        resolution=parsed.resolution,
        season=parsed.season,
        episode=str(parsed.episode) if parsed.episode is not None else None,
        number=canonical_episode_number(
            str(parsed.episode) if parsed.episode is not None else None
        ),
        version=parsed.version or 1,
        crc32=getattr(parsed, "crc32", None),
        tags=tuple(parsed.tags or ()),
        folder_name=folder,
        size=result.size,
        seeders=result.seeders,
        magnet_url=result.magnet,
        torrent_url=result.link,
    )
    if not parsed.title:
        row.reason = NO_TITLE
        return row
    if row.number is None:
        # A batch or a range. Not an episode, and the reason is worth showing:
        # "the only thing this group has is a batch" is a real answer.
        row.reason = NO_EPISODE
        # A season published as one torrent usually carries no range at all
        # ("[Judas] Azur Lane (Season 1) [1080p]"). Whether it is this show is
        # decided here, so that the dialog may offer to read its files (213).
        if parsed.episode is None:
            row.whole_season = matches_selected_folder(parsed, anime, scan.reference, scan.queries)
        return row
    # The names this scan searched under are the show's other spellings, and
    # a release carrying one of them is the show. Equality after
    # normalisation, so a short query admits nothing.
    if not matches_selected_folder(parsed, anime, scan.reference, scan.queries):
        row.reason = OTHER_SHOW
        return row
    unchecked: list[str] = []
    accepted, failures = profile_accepts(
        session,
        anime,
        parsed,
        scan.subgroup,
        scan.resolution,
        scan.reference,
        scan.queries,
        scan.edition,
        scan.edition_not,
        warnings=unchecked,
    )
    if not accepted:
        row.reason = PROFILE
        row.detail = failures
        return row
    row.accepted = True
    row.warnings = unchecked
    return row


__all__ = [
    "NO_EPISODE",
    "QueryOutcome",
    "NO_TITLE",
    "OTHER_SHOW",
    "PROFILE",
    "UNREADABLE",
    "ScannedRelease",
    "SelectionScan",
    "canonical_episode_number",
    "matches_selected_folder",
    "profile_accepts",
    "scan_selection",
    "stored_rules",
]

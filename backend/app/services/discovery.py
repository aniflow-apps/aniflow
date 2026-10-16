"""Anime discovery through the indexers the user configured.

No site is built in: an indexer is a name and a URL, added in the UI. What
comes back is parsed, matched and tagged by the same pipeline that handles feed
items, so a release found by searching is judged exactly like one that arrived
on its own.
"""
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.db.session import SessionLocal
from backend.app.integrations.search.base import SearchError, SearchProvider, SearchRequest, SearchResult
from backend.app.integrations.search.feed_indexer import (
    FeedIndexerProvider,
    NoIndexerConfigured,
)
from backend.app.models.anime import Anime, EpisodeStatus
from backend.app.models.indexer import Indexer
from backend.app.models.search_result import SearchDecision, SearchResult as SearchResultModel
from backend.app.services.app_settings import AppSettings, load_app_settings
from backend.app.services.attention import log_activity
from backend.app.services.indexer_definitions import resolve_search_template
from backend.app.services.episode_state import refresh_episode_status
from backend.app.services.feed_search import _evaluate_parsed
from backend.app.services.release_parser import normalize_title_for_match, parse_release_name

logger = for_component("Search")

#: How long to wait between two requests to the same indexer during a fan-out.
#: Trackers do not ban for this, but they do rate-limit, and a dozen requests
#: arriving together is what a rate limiter is for. One at a time with a gap
#: between them is what a person browsing the site looks like, and a debug
#: search that takes fifteen seconds is not a problem.
INDEXER_PAUSE_SECONDS = 1.0

#: Between two pages of the *same* term (build 105).
#:
#: Paging to exhaustion makes the spacing most of the wait, and this is the
#: pace agreed for it. Still strictly one request after another - never
#: parallel, which is the part that matters to a rate limiter. Switching to a
#: different term still costs the full second above.
PAGE_PAUSE_SECONDS = 0.5

#: Where a term that never ends is stopped.
#:
#: Not a result cap and not a judgement about how much is enough - the ceiling
#: exists so that an indexer answering every page with the same page cannot
#: loop for ever. At Nyaa's 75 hits a page this is fifteen thousand releases
#: for one term, which no real search reaches.
MAX_PAGES_GUARD = 200

#: How many results to take from one page of an indexer's answer.
#:
#: This was 50 at all four call sites, and Nyaa serves 75 items a page - so a
#: third of every page was discarded before anything looked at it. It cost
#: nothing to fix and it applies to every search AniFlow makes: the add-anime
#: preview, the per-episode search, the release inspector.
#:
#: Set above any page size a tracker is likely to serve rather than at exactly
#: 75: the number is a safety valve against a site that answers with
#: thousands, not a page size AniFlow should pretend to know. What actually
#: limits a search is search_max_pages and search_max_results.
RESULTS_PER_PAGE = 200

#: How a release found by searching is recorded. Not a site name: which site
#: was asked is the indexer's business, and one row outliving one indexer must
#: not leave a dangling brand in the data.
INDEXER_PROVIDER = "indexer"


@dataclass
class TitleGroup:
    """One title, from one subgroup, at one quality.

    A row used to be a title with lists of the groups and qualities it had
    been seen in, which is not something you can choose: "SubsPlease, ToonsHub
    | 1080p, 720p" does not say whether ToonsHub has 1080p. What the setup
    step actually decides is one combination, so that is what a row is.
    """

    title: str
    count: int
    #: None when the release name carries no group, or the parser could not
    #: read one. Never guessed - an unknown group is shown as unknown.
    subgroup: str | None = None
    resolution: str | None = None


@dataclass
class TraceEntry:
    """One release the indexer returned, and what became of it.

    Counts are the whole point of the preview - "SubsPlease 1080p, 12 files" -
    and a count you cannot check is a count you have to take on faith. Two
    things can make it look wrong, and they need different fixes: the indexer
    returned less than you expected (the query, the paging, the site), or it
    returned plenty and AniFlow dropped some (a name the parser cannot read, a
    duplicate, the result cap). This says which, per release.
    """

    query: str
    page: int
    raw_title: str
    #: Empty when the release was counted; otherwise why it was not.
    skipped: str = ""
    title: str | None = None
    subgroup: str | None = None
    resolution: str | None = None
    episode: str | None = None


@dataclass
class TitleSearchOutcome:
    """What a preview search found, and what it could not reach.

    The empty list used to be the only answer, which made "the indexer found
    nothing" and "the indexer never answered" indistinguishable. `failures`
    carries one line per attempt that failed; `reached_provider` is False when
    *no* attempt succeeded, which is the case the UI has to say out loud.
    """

    groups: list[TitleGroup] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    reached_provider: bool = True
    #: Filled only when the caller asks for it, because it is one entry per
    #: release and nothing but the debug export ever reads it.
    trace: list[TraceEntry] = field(default_factory=list)
    #: What was actually asked, in order. The searches AniFlow ran are the
    #: first thing to check when a count looks wrong.
    queries: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """True when nothing was found *and* nothing could be searched."""
        return not self.reached_provider

    @property
    def message(self) -> str | None:
        """One line describing the failure, or None when there was none."""
        return "; ".join(self.failures) if self.failures else None


@dataclass
class SearchSummary:
    considered: int = 0
    matched: int = 0
    rejected: int = 0
    ignored: int = 0
    ambiguous: int = 0
    skipped: int = 0
    attention: int = 0


def _search_settings() -> AppSettings:
    """The indexer settings that are actually in force.

    The five ``search_*`` values live in ``aniflow.yaml`` and are editable from
    the Settings page, so they have to be read from there and not from the
    environment defaults - which is what every call site here used to do,
    making the whole section of that page decorative.
    """
    return load_app_settings()


def _get_provider(
    settings: AppSettings | None = None,
    *,
    session: Session | None = None,
    native: bool = False,
    indexer_id: str | None = None,
) -> SearchProvider:
    """The indexer that searches ask.

    ``indexer_id`` names one when the caller knows which site it wants - the
    one picked in Choose release, or the one an anime was added from. An id
    that is gone or switched off falls back to the default rather than
    failing: an anime must not stop being searched because its site was
    removed.

    Configured by the user, never shipped: with several enabled the default is
    asked, and with none at all this raises :class:`NoIndexerConfigured` so the
    caller can offer to add one instead of reporting a failure nobody can act
    on. The timeout stays an environment concern - it is about this
    deployment's network, not about which site to ask.

    ``native`` asks for the template covering releases named in the original
    language. On a site that files by translation language they are in another
    category, and searching with a Japanese title without this finds nothing.
    """
    owns_session = session is None
    db = session or SessionLocal()
    try:
        indexer = None
        if indexer_id:
            indexer = db.scalars(
                select(Indexer).where(Indexer.id == indexer_id, Indexer.enabled.is_(True))
            ).first()
        if indexer is None:
            indexer = db.scalars(
                select(Indexer)
                .where(Indexer.enabled.is_(True))
                .order_by(Indexer.is_default.desc(), Indexer.created_at)
            ).first()
        if indexer is None:
            raise NoIndexerConfigured(
                "No indexer has been added yet. Add one on the Indexer Profiles page."
            )
        # A bare site address says nothing about how that site wants to be
        # asked a question; a definition file supplies that. A URL with its own
        # query string is left alone - the user already said how to search.
        template, definition = resolve_search_template(indexer.url, native=native)
        if definition is not None:
            logger.debug("indexer %s uses the %s definition", indexer.name, definition.name)
            if native and not definition.sorts_by_language:
                # Said rather than silently ignored: the caller asked for the
                # other category and this site has no other category, so the
                # answer will be about the ordinary one.
                logger.info(
                    "indexer %s has no separate category for untranslated releases",
                    indexer.name,
                )
        provider = FeedIndexerProvider(
            template,
            name=indexer.name,
            timeout=get_settings().search_request_timeout_seconds,
            # Where this site states its own result count, when its file says.
            total_template=definition.resolve_total(indexer.url, native=native)
            if definition is not None
            else None,
            total_pattern=definition.total_pattern if definition is not None else None,
            # And its own search page, which is what a search reads when the
            # file supplies one - the feed cannot page on every site.
            listing_template=definition.resolve_listing(indexer.url, native=native)
            if definition is not None
            else None,
            listing_item=definition.listing_item if definition is not None else None,
        )
        # Which file said all that, for the page to name when something it
        # needs is not in there.
        provider.definition_file = definition.source_file if definition is not None else None
        return provider
    finally:
        if owns_session:
            db.close()


def _evaluate_search_result(
    session: Session,
    anime: Anime,
    result: SearchResult,
) -> tuple[SearchDecision, list[str], list[str]]:
    """Evaluate one search result against the anime's profile.

    Returns (decision, failure_reasons, ambiguous_anime_ids).
    """
    if anime.profile is None:
        return SearchDecision.IGNORED, ["no_profile"], []

    try:
        parsed = parse_release_name(result.title)
    except ValueError as exc:
        logger.debug("could not parse search result %s: %s", result.title, exc)
        return SearchDecision.IGNORED, ["unparseable"], []

    evaluated = _evaluate_parsed(session, anime, anime.profile, parsed)

    # Map FeedSearchItem decision to SearchDecision
    decision_map = {
        "MATCHED": SearchDecision.MATCHED,
        "REJECTED": SearchDecision.REJECTED,
        "IGNORED": SearchDecision.IGNORED,
        "SKIPPED": SearchDecision.SKIPPED,
        "ATTENTION": SearchDecision.ATTENTION,
    }
    decision = decision_map.get(evaluated.decision, SearchDecision.IGNORED)

    failures = list(evaluated.failures)
    # No ambiguity to report: this search was made *for* one anime, so which
    # anime a hit belongs to is not in question. The RSS scan asks that - it
    # sees every anime's releases at once - and reading its answer off a
    # FeedSearchItem crashed the whole search pass on its first result, every
    # time it ran, since the field has never existed.
    return decision, failures, []


def _canonical_episode_number(number: str) -> str | None:
    stripped = number.strip()
    return str(int(stripped)) if stripped.isdigit() else None


def _no_indexer_result(exc: Exception) -> "SearchSummary":
    """An empty summary for "there is nothing to search".

    A search with no indexer configured is not a failure of the search - the
    caller has simply not been given anywhere to look, which the UI reports
    with an offer to add one.
    """
    summary = SearchSummary()
    summary.skipped = 1
    return summary


@dataclass
class InspectedRelease:
    """One search hit, exactly as it arrived, plus what the parser made of it.

    ``parsed`` is None when the name could not be read at all - which is the
    single most useful thing this can report, because a release the parser
    cannot read is invisible everywhere else in AniFlow.
    """

    id: str
    raw_name: str
    size: int | None = None
    seeders: int | None = None
    published_at: str | None = None
    parsed: dict[str, object] | None = None
    #: Why this hit would never reach a row: unreadable, or no title in it.
    skipped: str | None = None
    #: Which of the search terms turned this up. Often several - and when only
    #: one did, that is the finding: it says the broad term never saw it.
    found_by: list[str] = field(default_factory=list)
    #: What a library folder for this release would be called - the parsed
    #: title with its season, which is what the setup step offers as the
    #: folder name. None when the name yields nothing usable.
    folder_name: str | None = None


@dataclass
class InspectQuery:
    """One term that was searched, and what asking it achieved.

    ``found`` counts the rows the indexer handed back, duplicates included -
    two pages of 75 are 150 whether or not they held the same files. ``new``
    counts the releases nothing had returned before, which is what the term
    actually contributed: a term with a high ``new`` is earning its request,
    one with ``new`` at zero is asking a question already answered.

    ``pages`` is how many requests were made for this term. ``site_total`` is
    the number the site itself reports for the same search, when its
    definition file says where to read that from - "700 there, 75 here" is a
    fact about the indexer that no count on this side can express. And
    ``repeated`` marks the term whose walk stopped because the indexer sent a
    page it had already sent, which is what a feed that cannot be paged does.
    """

    term: str
    found: int = 0
    new: int = 0
    pages: int = 0
    error: str | None = None
    site_total: int | None = None
    repeated: bool = False
    #: Every address this term was asked at, in order (build 107). The debug
    #: page hands them over to be opened and compared - which is how the
    #: difference between a feed and a search page was found.
    urls: list[str] = field(default_factory=list)
    #: How long this term took, in seconds (build 109). Measured here rather
    #: than in the browser because one request can carry several terms, and
    #: "which mode is fastest for what it finds" is the question being asked.
    seconds: float = 0.0


@dataclass
class InspectOutcome:
    releases: list[InspectedRelease] = field(default_factory=list)
    queries: list[InspectQuery] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    reached_provider: bool = True
    #: What the indexer's definition file lets a search do (build 108).
    #: Without a listing the search reads the feed, which on some sites serves
    #: one page whatever is asked of it; without a count the column that says
    #: how much is out there stays a dash. Both are lines in a file the
    #: deployment owns, so the page says which ones are missing rather than
    #: leaving the search looking broken.
    reads_listing: bool = False
    reports_total: bool = False
    definition_file: str | None = None


def inspect_title_search(
    query: str,
    *,
    extra_terms: Optional[list[str]] = None,
    native: bool = False,
    provider: Optional[SearchProvider] = None,
    max_pages: Optional[int] = None,
    max_results: Optional[int] = None,
    progress_token: str | None = None,
    term_index: int = 0,
    term_count: int = 0,
    indexer_id: str | None = None,
) -> InspectOutcome:
    """Search for one name and keep *everything*, for the Release Inspector.

    The difference from :func:`preview_title_search` is what is thrown away.
    That one answers "which row should the user pick", so it groups hits by
    subgroup and quality, keeps counts, and drops names it cannot read. This
    one answers "why is that release not showing up", so it drops nothing:
    every hit is returned with its full parse beside it and a note when the
    parser could make nothing of it.

    **Several narrow terms rather than one broad one.** A tracker's feed
    carries a fixed number of items per page, sorted by seeders, so a broad
    query spends that budget on the most-seeded few of *every* quality: for one
    show it returned two 1080p, two 720p and two 480p files and stopped, while
    the same search with "720p" appended returned all ten 720p files. The
    narrower question fits its answer inside the page.

    So the caller passes the extra terms to try. Each is appended to the name,
    searched separately, and the results are merged and deduplicated - and the
    plain name is always asked as well, because a release whose name carries no
    quality at all would be missed by every one of the quality terms.

    Deliberately slow. The requests go one at a time with a second between
    them and each term gets a single page, because trackers rate-limit and a
    dozen requests arriving together is exactly what that is for. A dozen
    terms therefore takes something like twelve seconds, which is a fine price
    for a button somebody presses on purpose.

    ``native`` searches the category a site keeps its untranslated releases
    in. On Nyaa the ordinary template pins ``c=1_2`` - English-translated -
    so a search with a Japanese title was looking in a category those releases
    are never in and finding nothing at all.

    Nothing is filtered here. The two panes on the page filter and group in the
    browser, so changing a filter is instant and never costs another search -
    which matters when the question is "what does this filter exclude".
    """
    settings = _search_settings()
    try:
        # The id goes only when there is one: the tests replace this function
        # with stand-ins that know nothing of it.
        extra = {"indexer_id": indexer_id} if indexer_id else {}
        provider = provider or _get_provider(settings, native=native, **extra)
    except NoIndexerConfigured as exc:
        return InspectOutcome(failures=[str(exc)], reached_provider=False)

    # No ceiling unless the caller sets one (build 105). The page's whole
    # job is to show what the indexer has, and a limit chosen here is a limit
    # nobody asked for: the tracker said 383 and AniFlow showed 75.
    pages_per_term = max_pages or MAX_PAGES_GUARD
    results_limit = max_results  # None means "everything the tracker returns"
    name = (query or "").strip()
    if not name:
        return InspectOutcome()

    from backend.app.services import search_progress

    search_progress.start(progress_token, term_index=term_index, term_count=term_count)
    outcome = InspectOutcome()
    outcome.reads_listing = bool(getattr(provider, "reads_listing", False))
    outcome.reports_total = bool(getattr(provider, "reports_total", False))
    outcome.definition_file = getattr(provider, "definition_file", None)
    seen: set[str] = set()
    by_id: dict[str, InspectedRelease] = {}
    terms = _terms(name, extra_terms)
    first_request = True
    # Whether this indexer's feed pages at all (build 106). Nyaa's does not:
    # its RSS answers ?p=2 with the first page again, whatever its search
    # page does - confirmed against the deployment's own indexer. Once one
    # term has shown that, the terms after it are asked for one page each
    # rather than each spending a request to rediscover it: a six-name search
    # was paying five wasted requests for an answer already known.
    feed_pages = True
    for term in terms:
        record = InspectQuery(term=term)
        outcome.queries.append(record)
        started = time.monotonic()
        search_progress.asking(progress_token, term)
        request = SearchRequest(anime=term)
        # What the site itself says this search has, when its definition file
        # says where that number is - one extra request, and only from here.
        # A feed has nowhere to put "700", and "700 there, 75 here" is the
        # whole question this page is for.
        if getattr(provider, "reports_total", False):
            if not first_request and PAGE_PAUSE_SECONDS > 0:
                time.sleep(PAGE_PAUSE_SECONDS)
            first_request = False
            record.site_total = provider.total(request)
            # The site's own count is the best estimate of how many pages this
            # term will take, and it arrives before the first of them.
            search_progress.page_done(
                progress_token,
                expected=_pages_for(record.site_total) if record.site_total else None,
            )
            asked = getattr(provider, "last_url", None)
            if asked and asked not in record.urls:
                record.urls.append(asked)
        # How many hits the indexer puts on a full page, learned from its own
        # first answer rather than assumed. A later page holding fewer than
        # that is the last one, which saves asking for the empty page after
        # it - the difference between six requests and seven for a listing of
        # 383. Nothing is assumed about the number itself: Nyaa says 75, some
        # other site will say something else, and both work.
        page_size: int | None = None
        # What this term has already been given. Separate from `seen`, which
        # is every term's hits together: a page holding nothing *this term*
        # has not had is the tracker repeating itself, while a page of
        # releases another term already found is perfectly ordinary and says
        # nothing about whether there is a page after it.
        seen_for_term: set[str] = set()
        for page in range(1, (pages_per_term if feed_pages else 1) + 1):
            if results_limit is not None and len(outcome.releases) >= results_limit:
                break
            # Spaced out rather than fired together: sequential is what the
            # loop already is, and the gap is what keeps a long walk through
            # the pages from arriving as a burst. A new term waits the full
            # second; another page of the one already being asked waits half.
            if not first_request:
                pause = PAGE_PAUSE_SECONDS if page > 1 else INDEXER_PAUSE_SECONDS
                if pause > 0:
                    time.sleep(pause)
            first_request = False
            try:
                results = provider.search(request, page=page, limit=RESULTS_PER_PAGE)
            except SearchError as exc:
                logger.warning("inspector search failed for %r: %s", term, exc)
                record.error = str(exc)
                outcome.failures.append(f"{term}: {exc}")
                break
            finally:
                # Recorded whether it worked or not: the address of a request
                # that failed is the one most worth looking at.
                asked = getattr(provider, "last_url", None)
                if asked and asked not in record.urls:
                    record.urls.append(asked)
            record.pages += 1
            search_progress.page_done(progress_token)
            if not results:
                break
            if page_size is None:
                page_size = len(results)
                search_progress.asking(
                    progress_token,
                    term,
                    expected=_pages_for(record.site_total, page_size)
                    if record.site_total
                    else 0,
                )
            # Whether this page was one this term had already been given -
            # which is what an indexer with no page marker in its template
            # does, answering every page with the first one. That and an empty
            # page are the two ways a tracker says there is no more, and both
            # are the tracker saying it rather than a number set here.
            repeated = True
            for result in results:
                if results_limit is not None and len(outcome.releases) >= results_limit:
                    break
                record.found += 1
                if result.id not in seen_for_term:
                    seen_for_term.add(result.id)
                    repeated = False
                # A hit several terms return is one release, and knowing which
                # terms found it is the point: a release only the narrow term
                # saw is a release the broad one cannot reach.
                existing = by_id.get(result.id)
                if existing is not None:
                    if term not in existing.found_by:
                        existing.found_by.append(term)
                    continue
                seen.add(result.id)
                record.new += 1
                entry = _inspect_one(result)
                entry.found_by = [term]
                by_id[result.id] = entry
                outcome.releases.append(entry)
            if repeated:
                # The indexer answered with a page it had already sent. Said
                # rather than merely stopped: it means this feed cannot be
                # paged, and the site's own count below is then a measure of
                # how much is out of reach.
                record.repeated = True
                feed_pages = False
                break
            if page_size is not None and len(results) < page_size:
                # A short page is the end of the listing.
                break
        record.seconds = round(time.monotonic() - started, 3)
    return outcome


def _pages_for(total: int | None, page_size: int = 75) -> int:
    """How many pages a result count of this size takes, at that page size."""
    if not total or page_size < 1:
        return 0
    return max(1, -(-total // page_size))


def _terms(name: str, extra_terms: Optional[list[str]]) -> list[str]:
    """The name on its own, then the name with each extra term appended.

    The plain name first and always: a release with no quality in its name is
    invisible to every quality term, and dropping it would trade one blind
    spot for another.
    """
    terms = [name]
    for extra in extra_terms or []:
        addition = (extra or "").strip()
        if not addition:
            continue
        combined = f"{name} {addition}"
        if combined not in terms:
            terms.append(combined)
    return terms


def _inspect_one(result) -> InspectedRelease:
    """One hit, with its parse or the reason there is not one."""
    from dataclasses import asdict as _asdict

    entry = InspectedRelease(
        id=result.id,
        raw_name=result.title,
        size=result.size,
        seeders=result.seeders,
        published_at=result.published_at.isoformat() if result.published_at else None,
    )
    try:
        parsed = parse_release_name(result.title)
    except ValueError as exc:
        # The reason a count is lower than the indexer's own result list, and
        # invisible everywhere else in AniFlow.
        entry.skipped = f"the name could not be read: {exc}"
        return entry
    entry.parsed = _asdict(parsed)
    entry.parsed["unknown_tags"] = list(parsed.unknown_tags)
    if not parsed.title:
        entry.skipped = "no title in the name"
        return entry
    # The name a library folder would get for this release, season included:
    # "Hell Mode S2" rather than "Hell Mode". Computed here because the rule
    # lives in services/library and the setup step needs the same answer the
    # import will later give.
    from backend.app.services.library import cleaned_release_folder

    try:
        entry.folder_name = cleaned_release_folder(parsed)
    except ValueError:
        entry.folder_name = None
    return entry


def preview_title_search(
    title_variants: list[str | None],
    *,
    subgroup: str | None = None,
    resolution: str | None = None,
    provider: Optional[SearchProvider] = None,
    max_pages: Optional[int] = None,
    max_results: Optional[int] = None,
    trace: bool = False,
) -> TitleSearchOutcome:
    """Search for several name variants of the same not-yet-added anime
    (English/Romaji/Japanese, say) plus a chosen subgroup/quality, and group
    the raw results by their parsed, episode-stripped title.

    Used by the add-anime setup step to preview what's actually out there
    before the anime is created - nothing here touches the database.
    """
    settings = _search_settings()
    try:
        provider = provider or _get_provider(settings)
    except NoIndexerConfigured as exc:
        # Not a failed search - there is nothing to search. Reported as an
        # outcome so the add-anime step can offer to add one.
        return TitleSearchOutcome(groups=[], failures=[str(exc)], reached_provider=False)
    max_pages = max_pages or settings.search_max_pages
    max_results = max_results or settings.search_max_results

    seen_ids: set[str] = set()
    #: (title, subgroup, resolution) -> the releases found for that combination.
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    #: title key -> how often each spelling of the name was seen.
    names: dict[str, Counter] = {}
    failures: list[str] = []
    trail: list[TraceEntry] = []
    queries: list[str] = []
    attempted = 0
    succeeded = 0

    for name in title_variants:
        name = (name or "").strip()
        if not name:
            continue
        request = SearchRequest(anime=name, subgroup=subgroup, resolution=resolution)
        queries.append(name)
        results_processed = 0
        # The fullest page seen; a shorter one is the last (no request for the
        # empty page after it).
        page_size = 0
        for page in range(1, max_pages + 1):
            if results_processed >= max_results:
                break
            attempted += 1
            try:
                results = provider.search(request, page=page, limit=RESULTS_PER_PAGE)
            except SearchError as exc:
                # Reported, not swallowed: an indexer that is unreachable,
                # rate-limiting or rejecting the query must not look like one
                # that simply has nothing.
                logger.warning("preview search failed for %r: %s", name, exc)
                failures.append(f"{name}: {exc}")
                break
            succeeded += 1
            if not results:
                break
            for result in results:
                if results_processed >= max_results:
                    break
                results_processed += 1
                if result.id in seen_ids:
                    if trace:
                        trail.append(TraceEntry(name, page, result.title, "already counted"))
                    continue
                seen_ids.add(result.id)
                try:
                    parsed = parse_release_name(result.title)
                except ValueError as exc:
                    # Silent until now, and the most likely reason a count is
                    # lower than the site's own result list.
                    if trace:
                        trail.append(TraceEntry(name, page, result.title, f"unreadable name: {exc}"))
                    continue
                if not parsed.title:
                    if trace:
                        trail.append(TraceEntry(name, page, result.title, "no title in the name"))
                    continue
                # Use cleaned folder name (with season) for display so
                # "Hell Mode" S2 shows as "Hell Mode S2" and maps to the
                # correct library folder. Group key stays season-agnostic
                # so S1/S2 still merge under "hell mode" but display the
                # most frequent season variant.
                from backend.app.services.library import cleaned_release_folder
                display = cleaned_release_folder(parsed)
                title_key = normalize_title_for_match(parsed.title)
                # The season-agnostic key still merges S1 and S2 spellings, so
                # the name shown is the one the majority of releases use rather
                # than whichever happened to arrive first.
                names.setdefault(title_key, Counter())[display] += 1
                key = (title_key, parsed.subgroup or "", parsed.resolution or "")
                bucket = groups.setdefault(
                    key,
                    {"count": 0, "subgroup": parsed.subgroup, "resolution": parsed.resolution},
                )
                bucket["count"] = int(bucket["count"]) + 1
                if trace:
                    trail.append(TraceEntry(
                        name, page, result.title, "",
                        title=display,
                        subgroup=parsed.subgroup,
                        resolution=parsed.resolution,
                        episode=str(parsed.episode) if parsed.episode is not None else None,
                    ))
            if page_size and len(results) < page_size:
                break
            page_size = max(page_size, len(results))

    ordered = sorted(
        (
            TitleGroup(
                title=names[title_key].most_common(1)[0][0],
                count=int(bucket["count"]),
                subgroup=bucket["subgroup"],  # type: ignore[arg-type]
                resolution=bucket["resolution"],  # type: ignore[arg-type]
            )
            for (title_key, _sg, _res), bucket in groups.items()
        ),
        # Most releases first, then a stable order so two runs of the same
        # search cannot disagree about which row comes first.
        key=lambda group: (
            -group.count,
            group.title.lower(),
            (group.subgroup or "").lower(),
            group.resolution or "",
        ),
    )
    return TitleSearchOutcome(
        groups=ordered,
        failures=failures,
        trace=trail,
        queries=queries,
        # Nothing was even attempted (no usable title variant) counts as
        # reached: there is no indexer problem to report in that case.
        reached_provider=succeeded > 0 or attempted == 0,
    )


def _by_frequency(counter: object) -> list[str]:
    """Names ordered by how often they occurred, most common first."""
    if not isinstance(counter, Counter):
        return []
    return [name for name, _count in counter.most_common()]


def _report_availability(
    session,
    anime: Anime,
    selected_folder: str,
    subgroup: str | None,
    resolution: str | None,
    created: int,
) -> None:
    """Say so when the indexer answers and nothing it returned fits.

    An anime with no releases looks like three different things at once: one
    whose episodes have not aired, one whose indexer is down, and one whose
    release name matches nothing. Only the last is a mistake somebody can
    fix, and it is the one that used to be invisible - on the deployment it
    lasted days, with ten episodes offering nothing and no explanation
    anywhere.

    Raised only when the indexer *did* answer: no indexer at all raises
    before this is reached, which is a different thing and is reported
    elsewhere. Closed as soon as anything is found, so the record cannot
    outlive the problem.
    """
    from backend.app.models.activity import AttentionKind
    from backend.app.services.attention import raise_attention, resolve_open

    if created:
        resolve_open(session, kind=AttentionKind.NO_RELEASES, anime_id=anime.id)
        return
    wanted = " ".join(part for part in (subgroup, resolution) if part) or "anything"
    raise_attention(
        session,
        kind=AttentionKind.NO_RELEASES,
        anime_id=anime.id,
        # The two things worth checking, in the order you would check them.
        detail=f"{wanted} | {selected_folder}"[:120],
    )
    logger.warning(
        "%s: the indexer returned nothing matching %r as %s",
        anime.display_name,
        selected_folder,
        wanted,
    )


def _other_names(anime: Anime) -> list[str]:
    """The show's other spellings, for a search that must not miss it.

    Titles only - not the folder name, and not the aliases the user added,
    which are matching hints rather than names a tracker would index.
    """
    return [
        name
        for name in (anime.title_romaji, anime.title_english, anime.local_name)
        if name and name.strip()
    ]


def persist_available_from_preview(
    session,
    anime: Anime,
    search_query: str,
    selected_folder: str,
    subgroup: str | None,
    resolution: str | None,
    edition: list[str] | None = None,
    edition_not: list[str] | None = None,
    known: list[SearchResult] | None = None,
) -> int:
    """Re-run the preview's query and attach the hits the profile accepts.

    Several releases per episode are kept - versions, and any qualities the
    profile allows - because choosing between *acceptable* ones belongs to
    queue time. What is not kept is a release the profile would refuse: the
    subgroup and quality reach the indexer as search *text*, every site matches
    text loosely, and the results come back with other groups mixed in. An
    anime set up as "Ironclad 1080p" listed `[sam]` and `[ToonsHub]` episodes
    as available, each with a Download button, which made the profile the user
    had just chosen describe nothing they could see.

    Nothing wrong was ever downloaded - queue time runs the same check - but
    the episode list is the answer to "what can I get", and it has to be true.

    Episode rows are created as needed so a release always has an owner.

    Returns the number of release rows created.
    """
    from sqlalchemy import select

    from backend.app.models.anime import Episode, EpisodeType
    from backend.app.models.episode_release import EpisodeRelease
    from backend.app.services.selection_scan import scan_selection

    # The dialog ran this exact scan when the row was selected, so what it
    # showed is what is written here. See services/selection_scan.py.
    scan = scan_selection(
        # Every name, not just the one that was typed. The dialog searched two
        # and the server searched one, so an anime whose group publishes under
        # the other spelling arrived with no episodes at all.
        queries=[search_query, *_other_names(anime)],
        reference=selected_folder,
        subgroup=subgroup,
        resolution=resolution,
        edition=edition or [],
        edition_not=edition_not or [],
        # What the dialog already found, when it sent it: the same verdicts
        # without asking the tracker twice for one answer (build 100).
        known=known,
        session=session,
        anime=anime,
    )
    if scan.no_indexer:
        log_activity(
            session,
            kind="INDEXER_MISSING",
            anime_id=anime.id,
            # *"I guess this means the Anime X has no indexer attached."*
            # The name is on its own line already; what was missing is the
            # consequence - nothing was searched for, and nothing will be
            # until an indexer exists.
            message=(
                "Added without searching: no indexer is configured, so no "
                "releases were looked for"
            ),
            details={"reason": "; ".join(scan.failures)},
        )
        return 0
    if scan.failures:
        # Recorded in Activity rather than only in the log: this runs while an
        # anime is being added, and "no releases yet" for an unreachable
        # indexer is a misleading thing to leave on screen.
        log_activity(
            session,
            kind="INDEXER_UNREACHABLE",
            anime_id=anime.id,
            # *"From my understanding the indexer in our case Nyaa is down
            # or not reachable. I think the description should explain this."*
            message=(
                "The indexer could not be reached, so no releases were found "
                f"for this anime: {scan.failures[0]}"
            ),
            details={"query": search_query, "error": "; ".join(scan.failures)[:250]},
        )

    # Through the anime's own numbering, never the raw one on the file
    # (build 113). This path builds its episodes itself rather than going
    # through episode_releases, so build 112's offset never reached it: a
    # watchlist entry for Re:Zero season 4 arrived with episodes 39 to 82 on
    # it instead of 1 to 19.
    offset = int(getattr(anime, "episode_offset", 0) or 0)
    hits = {}
    for release in scan.accepted:
        if release.number is None:
            continue
        number = int(release.number) - offset
        if number < 1:
            # An earlier season, under the same name, from a group that never
            # restarts its numbering.
            continue
        hits[release.id] = (number, release)
    if not hits:
        # The indexer answered and nothing it returned belongs to this anime.
        # That is the case worth reporting, and the one that used to be
        # indistinguishable from "nothing has aired yet".
        _report_availability(session, anime, selected_folder, subgroup, resolution, 0)
        session.commit()
        return 0

    episodes = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
    }
    for number, _release in hits.values():
        if number not in episodes:
            episode = Episode(
                anime_id=anime.id,
                number=number,
                episode_type=EpisodeType.REGULAR,
                status=EpisodeStatus.UNAVAILABLE,
            )
            session.add(episode)
            episodes[number] = episode
    session.flush()  # assign episode ids before the releases reference them

    max_found = max(number for number, _release in hits.values())
    if max_found > (anime.episode_count or 0):
        anime.episode_count = max_found

    created = 0
    for provider_id, (number, release) in hits.items():
        episode = episodes[number]
        existing = session.scalar(
            select(EpisodeRelease).where(
                EpisodeRelease.episode_id == episode.id,
                EpisodeRelease.provider == INDEXER_PROVIDER,
                EpisodeRelease.provider_id == provider_id,
            )
        )
        target = existing or EpisodeRelease(
            episode_id=episode.id, provider=INDEXER_PROVIDER, provider_id=provider_id
        )
        target.raw_name = release.raw_name[:500]
        target.magnet_url = release.magnet_url
        target.torrent_url = release.torrent_url
        target.size = release.size
        target.seeders = release.seeders
        target.subgroup = release.subgroup
        target.resolution = release.resolution
        target.version = release.version or 1
        if existing is None:
            session.add(target)
            created += 1

    session.flush()
    for episode in episodes.values():
        refresh_episode_status(episode)
    _report_availability(session, anime, selected_folder, subgroup, resolution, created)
    session.commit()
    logger.info(
        "persisted %d releases across %d episodes for anime %s (%s)",
        created,
        len({number for number, _release in hits.values()}),
        anime.id,
        selected_folder,
    )
    return created


def search_for_anime(
    session: Session,
    anime_id: str,
    *,
    provider: Optional[SearchProvider] = None,
    query_override: Optional[str] = None,
    max_pages: Optional[int] = None,
    max_results: Optional[int] = None,
) -> SearchSummary:
    """Search for releases for one anime and store evaluated results.

    Uses the anime's profile (subgroups, resolution, etc.) to evaluate each result.
    Results are stored in the search_results table with their decision.

    Args:
        session: Database session
        anime_id: Anime to search for
        provider: Optional provider override (uses configured default if None)
        query_override: Optional raw query string to use instead of template-based query
        max_pages: Max pages to fetch (uses config default if None)
        max_results: Max total results to process (uses config default if None)

    Returns:
        SearchSummary with counts per decision.
    """
    anime = session.get(Anime, anime_id)
    if anime is None:
        raise SearchError("Anime not found")

    profile = anime.profile
    if profile is None:
        logger.info("anime %s has no profile, skipping search", anime_id)
        return SearchSummary()

    settings = _search_settings()
    try:
        # The site this anime was chosen from, when it has one (build 219).
        extra = {"indexer_id": anime.indexer_id} if anime.indexer_id else {}
        provider = provider or _get_provider(settings, **extra)
    except NoIndexerConfigured as exc:
        logger.info("no indexer configured: %s", exc)
        return _no_indexer_result(exc)
    max_pages = max_pages or settings.search_max_pages
    max_results = max_results or settings.search_max_results

    # The name release groups publish under, not the one on the card
    # (build 101). The display name follows a setting about what to read on
    # screen - "KAMUI: He's Behind You" - while the tracker has "Ushiro no
    # Shoumen Kamui-san", and searching for the first finds nothing at all.
    # Same lesson as build 96, one pass along.
    from backend.app.services.release_identity import (
        anime_reference_name,
        strip_season,
    )

    # Season markers come off: groups carry them in the name rather than in
    # the title, so "… S1" as a search term matches less than the name alone.
    searchable = strip_season(anime_reference_name(anime)) or anime.display_name

    query = query_override
    if not query:
        parts = [searchable]
        if profile.subgroups:
            parts.append(profile.subgroups[0])  # use first subgroup
        if profile.resolution:
            parts.append(profile.resolution)
        query = " ".join(parts)

    request = SearchRequest(
        anime=searchable,
        subgroup=profile.subgroups[0] if profile.subgroups else None,
        resolution=profile.resolution,
        media_source=profile.media_source,
        censor_preference=profile.censor_preference.value,
    )

    summary = SearchSummary()
    results_processed = 0
    #: Everything the tracker returned, so the matches can be attached to
    #: their episodes without asking for them a second time.
    fetched: list[SearchResult] = []

    for page in range(1, max_pages + 1):
        if results_processed >= max_results:
            break

        try:
            results = provider.search(request, page=page, limit=RESULTS_PER_PAGE)
        except SearchError as exc:
            logger.warning("search page %d failed for anime %s: %s", page, anime_id, exc)
            break

        if not results:
            break  # no more results

        fetched.extend(results)
        for result in results:
            if results_processed >= max_results:
                break

            summary.considered += 1
            results_processed += 1

            # Check if already stored (deduplication by provider + provider_id)
            existing = session.scalar(
                select(SearchResultModel).where(
                    SearchResultModel.provider == INDEXER_PROVIDER,
                    SearchResultModel.provider_id == result.id,
                )
            )
            if existing:
                continue

            decision, failures, ambiguous = _evaluate_search_result(session, anime, result)

            search_result = SearchResultModel(
                provider=INDEXER_PROVIDER,
                provider_id=result.id,
                query_used=query,
                page=page,
                raw_name=result.title,
                torrent_url=result.link,
                magnet_url=result.magnet,
                size=result.size,
                seeders=result.seeders,
                leechers=result.leechers,
                published_at=result.published_at,
                category=result.category,
                parsed_title=result.title,  # will be replaced by parsed.title if available
                subgroup=result.title,  # placeholder, will be overwritten by parsed
                episode=None,
                version=1,
                resolution=None,
                media_source=None,
                decision=decision,
                anime_id=anime.id if decision in (SearchDecision.MATCHED, SearchDecision.ATTENTION) else None,
                failure_reasons=failures,
                ambiguous_anime_ids=ambiguous,
            )

            # Try to extract parsed fields for better search result display
            try:
                parsed = parse_release_name(result.title)
                search_result.parsed_title = parsed.title
                search_result.subgroup = parsed.subgroup
                search_result.episode = parsed.episode
                search_result.version = parsed.version
                search_result.resolution = parsed.resolution
                search_result.media_source = parsed.media_source
            except ValueError:
                pass  # keep raw fallbacks

            session.add(search_result)

            if decision == SearchDecision.MATCHED:
                summary.matched += 1
            elif decision == SearchDecision.REJECTED:
                summary.rejected += 1
            elif decision == SearchDecision.ATTENTION:
                summary.attention += 1
            elif decision == SearchDecision.SKIPPED:
                summary.skipped += 1
            elif decision == SearchDecision.AMBIGUOUS:
                summary.ambiguous += 1
            else:
                summary.ignored += 1

        session.commit()

    # Attach what was found to the episodes it belongs to (build 101).
    #
    # Without this the pass filled a table nobody downloads from: an anime
    # whose search came up empty when it was added stayed empty for ever,
    # while the record on the Activity page said AniFlow keeps looking. It
    # did keep looking - and nothing it found ever reached an episode.
    #
    # `known=` rather than a second search: these are the hits just fetched,
    # and the same code the dialog uses judges them.
    if fetched and anime.release_name:
        try:
            persist_available_from_preview(
                session,
                anime,
                search_query=query,
                selected_folder=anime.release_name,
                subgroup=None,
                resolution=None,
                known=fetched,
            )
        except Exception as exc:  # noqa: BLE001 - one anime must not end the pass
            logger.warning(
                "could not attach search results for %s: %s", anime.display_name, exc
            )

    logger.info(
        "search completed for anime %s: %s considered, %s matched, %s rejected, %s ignored, %s attention",
        anime_id,
        summary.considered,
        summary.matched,
        summary.rejected,
        summary.ignored,
        summary.attention,
    )
    return summary


def search_for_all_anime(
    session_factory,
    *,
    provider: Optional[SearchProvider] = None,
    max_pages: Optional[int] = None,
    max_results: Optional[int] = None,
) -> SearchSummary:
    """Search for all active/upcoming anime with profiles."""
    from backend.app.services.rss_scan import _hunting

    with session_factory() as gate:
        if not _hunting(gate, "search"):
            return SearchSummary()
    settings = _search_settings()
    #: Given by the caller (tests, one-off tools): it answers for every anime.
    #: Otherwise an anime that was chosen from a particular indexer asks that
    #: one, and the rest share the default.
    forced = provider
    try:
        provider = provider or _get_provider(settings)
    except NoIndexerConfigured as exc:
        logger.info("no indexer configured: %s", exc)
        return _no_indexer_result(exc)
    max_pages = max_pages or settings.search_max_pages
    max_results = max_results or settings.search_max_results

    session = session_factory()
    try:
        anime_list = list(
            session.scalars(
                select(Anime).where(
                    Anime.lifecycle.in_(["ACTIVE", "UPCOMING"]),
                    Anime.profile != None,  # noqa: E711
                    # Left alone on request (build 123). This is the pass
                    # that would otherwise spend an indexer query per cycle
                    # on a 1996 series nobody is waiting for.
                    Anime.releases_ignored.is_(False),
                )
            )
        )
        summary = SearchSummary()
        for anime in anime_list:
            result = search_for_anime(
                session,
                anime.id,
                provider=provider if forced or not anime.indexer_id else None,
                max_pages=max_pages,
                max_results=max_results,
            )
            summary.considered += result.considered
            summary.matched += result.matched
            summary.rejected += result.rejected
            summary.ignored += result.ignored
            summary.ambiguous += result.ambiguous
            summary.attention += result.attention
            summary.skipped += result.skipped
        return summary
    finally:
        session.close()


__all__ = [
    "TitleSearchOutcome",
    "SearchSummary",
    "TitleGroup",
    "TraceEntry",
    "preview_title_search",
    "search_for_anime",
    "search_for_all_anime",
]
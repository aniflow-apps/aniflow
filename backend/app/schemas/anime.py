from datetime import date, datetime

from pydantic import AliasChoices, BaseModel, Field, computed_field, field_validator

from backend.app.models.anime import AnimeLifecycle, EpisodeStatus, EpisodeType
from backend.app.services.release_parser import search_term_for_title


class AnimeSearchResult(BaseModel):
    provider: str
    provider_id: str
    display_name: str
    title_english: str | None = None
    title_romaji: str | None = None
    title_native: str | None = None
    aliases: list[str] = []
    cover_url: str | None = None
    genres: list[str] = []
    #: Who made it, and what it is about - the detail view's own fields
    #: (build 101). Empty for an entry from a source that carries neither.
    studios: list[str] = []
    synopsis: str | None = None
    #: How this relates to seasons already tracked: sequel | gap | prequel,
    #: or null (build 101). Null also when this very season is tracked -
    #: `in_library` says that, and saying it twice would be two marks on one
    #: card.
    related: str | None = None
    format: str | None = None
    episode_count: int | None = None
    #: Not started, running, over, abandoned, or nobody said (build 148).
    #: One of ``integrations/metadata/airing.AIRING_STATUSES``.
    #:
    #: The difference between "starts next week" and "finished two years
    #: ago", which a premiere date alone does not give you: a season page
    #: lists shows whose dates are all in the same quarter, and half of them
    #: have not begun.
    airing_status: str = "UNKNOWN"
    is_adult: bool = False
    #: When it premieres, and one instant an episode airs (build 86). Sent so
    #: the calendar can order a season by date without asking again - it has
    #: always had both values and simply never passed them on.
    air_start_date: date | None = None
    air_slot_at: datetime | None = None
    #: Which episode ``air_slot_at`` is the airing time of (build 146). With
    #: it, every other episode is that instant plus seven days each way - see
    #: services/calendar_schedule.py.
    air_slot_episode: int | None = None
    #: How many people have it on a list, added up across every source that
    #: published a number. Not a real quantity - see AnimeCandidate - and used
    #: for one thing: ordering a season.
    popularity: int | None = None
    provider_ids: dict[str, str] = {}
    in_library: bool = False
    #: When ``in_library``: the watchlist anime it was matched to and the rule
    #: that matched ("title: ... -> ...", "pair: anilist 123"). For finding out
    #: why a show says it is already tracked; null otherwise.
    in_library_as: str | None = None
    in_library_why: str | None = None
    #: The Calendar's own stable id (build 143), e.g. ``al54``. Minted once
    #: and never changed, so a client - a television app, most of all - can
    #: store it indefinitely. None for a search result, which is not a
    #: calendar entry and has no such identity.
    calendar_id: str | None = None
    #: Whether the add flow can actually fetch details for this entry.
    #:
    #: The calendar knows about shows from five sources; adding one needs a
    #: provider AniFlow can query, which today means AniList or Kitsu. A show
    #: only MyAnimeList and Shikimori describe is real and worth showing and
    #: cannot be added, and saying so on the card beats failing on the click.
    addable: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def search_hint(self) -> str:
        """What to prefill the setup dialog's search term with.

        Follows ``title_search_order`` from the settings - the setting that was
        stored for exactly this and until now decided nothing. Pick English and
        the box offers "BLEACH Thousand-Year Blood War - The Calamity"; pick
        romaji and it offers "BLEACH Sennen Kessen-hen - Kashin-tan". Each is
        then cleaned the same way, because the season designation and the colon
        come off whichever name you chose.

        A computed field rather than something the routes fill in: the search
        endpoint and the calendar both hand these to the same dialog, and a
        prefill that silently depends on which endpoint you came through would
        be the sort of difference nobody thinks to look for.

        Still only a starting point - the field is editable, and what the user
        types wins.
        """
        from backend.app.services.app_settings import load_app_settings
        from backend.app.services.title_preference import (
            DEFAULT_SEARCH_ORDER,
            from_anime,
            parse_order,
            pick,
        )

        order = parse_order(load_app_settings().title_search_order, DEFAULT_SEARCH_ORDER)
        return search_term_for_title(pick(from_anime(self), order))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def folder_hint(self) -> str:
        """What to prefill the setup dialog's folder name with.

        Follows ``title_folder_order``, which is the setting about names on
        disk. Separate from ``display_name`` since build 85, when that started
        following a setting of its own: without this, choosing to *read*
        Japanese titles on screen would have started naming folders in
        Japanese, which is a decision nobody made.
        """
        from backend.app.services.app_settings import load_app_settings
        from backend.app.services.title_preference import (
            DEFAULT_FOLDER_ORDER,
            from_anime,
            parse_order,
            pick,
        )

        order = parse_order(load_app_settings().title_folder_order, DEFAULT_FOLDER_ORDER)
        return pick(from_anime(self), order)


class AddAnimeRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    provider_id: str = Field(min_length=1, max_length=80)
    local_name: str = Field(min_length=1, max_length=180)
    display_name: str = Field(min_length=1, max_length=255)
    # How releases name this anime - the result the user picked in the setup
    # step. Separate from local_name/media_folder, which the user may rename
    # freely without breaking matching. Falls back to local_name when omitted.
    release_name: str | None = Field(default=None, max_length=255)
    lifecycle: AnimeLifecycle = AnimeLifecycle.ACTIVE
    #: The library this goes into (build 192). Omitted means the default one -
    #: nothing is added without a library.
    library_id: int | None = None
    # Set explicitly by the add-anime setup step. When omitted, the anime
    # falls back to whatever default ProfileTemplate/FeedSource exist.
    subgroup: str | None = Field(default=None, max_length=100)
    resolution: str | None = Field(default=None, max_length=20)
    #: Which copy of each episode the picked row was: ["uncensored",
    #: "dual-audio"] (build 97). Stored on the profile, so the queue takes the
    #: same copy later that the dialog showed.
    edition: list[str] = Field(default_factory=list, max_length=12)
    #: What the other copies are recognised by, which this one is not
    #: (build 98). Required as well as `edition`: the plain copy carries
    #: everything the dual-audio row asks for minus one tag.
    edition_not: list[str] = Field(default_factory=list, max_length=12)
    # The template the values above came from. Without it the profile cannot be
    # linked back and the detail page reports "Custom (manual)".
    template_id: str | None = Field(default=None, max_length=36)
    feed_source_id: str | None = None
    # Search term override used in the setup preview (editable field).
    # When set, the preview query that found the selected folder is re-run
    # and the per-episode hits are saved as AvailableRelease for the detail view.
    search_query: str | None = Field(default=None, max_length=255)
    #: The tickets the setup dialog's scan handed out (build 100) - one per
    #: name it searched under. With them, the releases it already found are
    #: written down; without them - a script, an expired ticket, a restart -
    #: the search is repeated, which is what every build up to 99 did.
    scan_ids: list[str] = Field(default_factory=list, max_length=8)
    #: How far this group's numbering runs past this season's episode 1
    #: (build 112). 66 for Re:Zero season 4 under SubsPlease, whose first file
    #: for it is called 67. Zero - the default - is every ordinary case.
    episode_offset: int = Field(default=0, ge=0, le=10000)
    #: How many episodes this season has, when the user corrected the
    #: provider's count in the setup dialog (build 176). Stored as the user's
    #: own count, the same as editing it afterwards.
    episode_count: int | None = Field(default=None, ge=1, le=10000)
    #: Add this as a future release, with no download configuration (121).
    #:
    #: Sent by the setup dialog when the show's first episode is still ahead
    #: of us: there is nothing to search for, nothing to match, and no
    #: profile worth writing. The entry gets every fact about the show and is
    #: offered for conversion once its premiere has passed.
    #:
    #: Ignored when there is no indexer at all - that is a basic entry, which
    #: is a different situation with no end date. See services/app_mode.py.
    future: bool = False
    #: AniDB's id for this show, when the setup dialog knows it (build 116).
    #:
    #: It usually does: the dialog resolves it to work out this group's
    #: numbering, and it is the id Shoko Server states for everything it
    #: holds. Sent rather than resolved again on the server, because the
    #: dialog already had to decide *which* AniDB entry this season is and a
    #: second guess could land on a different one. Omitted, the server looks
    #: it up in the dump itself.
    anidb_id: int | None = Field(default=None, ge=1)
    #: The indexer the picked release came from (build 219). Stored on the
    #: anime so the searches for its later episodes ask the same site; an
    #: adult indexer also marks the anime Adult. Omitted means the default.
    indexer_id: str | None = Field(default=None, max_length=36)


class SetupSearchRequest(BaseModel):
    title_english: str | None = Field(default=None, max_length=255)
    title_romaji: str | None = Field(default=None, max_length=255)
    title_native: str | None = Field(default=None, max_length=255)
    subgroup: str | None = Field(default=None, max_length=100)
    resolution: str | None = Field(default=None, max_length=20)
    # Custom search term override. When set, it replaces
    # the three title variants above as the sole query term.
    query: str | None = Field(default=None, max_length=255)


class TitleGroupResult(BaseModel):
    """One title from one subgroup at one quality - a row you can pick."""

    title: str
    count: int
    #: Null when the release name carries no group the parser could read.
    subgroup: str | None = None
    resolution: str | None = None


class SetupSearchResponse(BaseModel):
    groups: list[TitleGroupResult]
    #: Set when the indexer could not be searched at all. An empty `groups`
    #: with this unset means "searched, found nothing"; with this set it means
    #: "never got an answer" - two very different things to tell the user.
    indexer_error: str | None = None


class SelectionScanRequest(BaseModel):
    """One row of the setup dialog, asked about specifically (build 95).

    The broad preview search is a sample: a tracker sorts a page by seeders
    and cuts it off, so a name searched on its own shows a few files from
    every group. This asks the narrow question instead - one name, one group,
    one quality - which is the question whose answer fits in a page, and the
    same question the server asks when the anime is added.

    ``reference`` is the folder that was picked. It decides which season a
    release has to be from, so it is sent rather than derived: the row knows
    what it offered.
    """

    #: Every name to search under. The dialog sends the same ones it
    #: searched with, so the number in the box is the number that gets
    #: stored - see services/selection_scan.py.
    queries: list[str] = Field(min_length=1, max_length=6)
    reference: str = Field(min_length=1, max_length=255)
    subgroup: str | None = Field(default=None, max_length=100)
    resolution: str | None = Field(default=None, max_length=20)
    #: Which copy of each episode: ["uncensored", "dual-audio"] (build 97).
    #: One group publishes three files per episode, and the row that was
    #: picked names one of them.
    edition: list[str] = Field(default_factory=list, max_length=12)
    #: What the other copies are recognised by, which this one is not
    #: (build 98). Required as well as `edition`: the plain copy carries
    #: everything the dual-audio row asks for minus one tag.
    edition_not: list[str] = Field(default_factory=list, max_length=12)
    #: An anime that already exists, when the dialog is changing its release
    #: rather than adding it. Its profile then takes part in the verdicts.
    anime_id: str | None = Field(default=None, max_length=64)
    #: Which indexer to ask (build 219); omitted means the default one.
    indexer_id: str | None = Field(default=None, max_length=36)


class ScannedReleaseResult(BaseModel):
    """One file the scan looked at, and what it decided about it.

    Rejected files are carried too, with the reason. A box that comes back
    empty while the tracker plainly has files is the case worth explaining,
    and it cannot be explained by a list of what survived.
    """

    #: The search hit's own id, so a batch row can be asked about by the ticket and this id (build 213).
    id: str | None = None
    #: A whole-season torrent: no single episode, a range in its name. The dialog can read what is inside.
    batch: bool = False
    raw_name: str
    subgroup: str | None = None
    resolution: str | None = None
    season: int | None = None
    episode: str | None = None
    #: The episode number this file is, or null for a batch or a range.
    number: int | None = None
    version: int = 1
    crc32: str | None = None
    folder_name: str | None = None
    #: Everything the name says about itself that no other field accounted
    #: for. Which of these actually distinguish the copies is decided from
    #: the whole set - see frontend/src/editions.ts.
    tags: list[str] = []
    size: int | None = None
    seeders: int | None = None
    accepted: bool = False
    #: unreadable | no-title | no-episode | other-show | profile
    reason: str | None = None
    #: What the profile objected to, in the evaluator's own words.
    detail: list[str] = []
    #: What the profile asks for and the name does not say, on a file that was
    #: accepted anyway (build 220): ``resolution_unknown``.
    warnings: list[str] = []


class QueryOutcomeResult(BaseModel):
    """One name searched, and what asking under it achieved."""

    term: str
    found: int = 0
    new: int = 0
    error: str | None = None


class StoredRule(BaseModel):
    """One rule of the anime's stored profile that the scan applies (item 48)."""

    #: resolution | media_source | censor | edition | edition_not
    kind: str
    value: str


class SelectionScanResponse(BaseModel):
    #: Tickets for what this scan found, good for half an hour. Adding sends
    #: them back so the tracker is not asked twice for one answer - see
    #: services/scan_store.py. Empty when there was nothing to keep.
    scan_ids: list[str] = []
    releases: list[ScannedReleaseResult] = []
    #: One entry per name searched. A name that found nothing while another
    #: found the season is the most useful line in the window.
    asked: list[QueryOutcomeResult] = []
    #: The episode numbers that would be created, sorted.
    #:
    #: What these numbers *mean* - whether a group counts from the start of
    #: the franchise rather than from this season - is worked out in the
    #: browser (frontend/src/selectionView.ts). It is presentation, and the
    #: dialog asks one name at a time and merges the answers, so anything
    #: derived here would be derived from a fraction of the result.
    episodes: list[int] = []
    pages: int = 0
    failures: list[str] = []
    reached_provider: bool = True
    #: True when there is no indexer at all. Not an error: an anime can be
    #: added without one and fed from RSS instead.
    no_indexer: bool = False
    #: What the anime's stored profile also requires, beyond what the dialog asked (item 48).
    stored_rules: list[StoredRule] = []


class InspectSearchRequest(BaseModel):
    """What to search, and how. See the Release Inspector.

    ``extra_terms`` turns one broad search into several narrow ones: each is
    appended to the name and searched separately, and the results are merged.
    A tracker's feed carries a fixed number of items per page sorted by
    seeders, so a broad query spends that budget on the most-seeded few of
    every quality - which is how a search returned two files per quality while
    the same search with "720p" appended returned all ten.

    ``native`` searches the category a site keeps untranslated releases in.
    Without it a Japanese title is looked for among the English-translated
    releases, where it will never be.
    """

    query: str = Field(min_length=1, max_length=255)
    extra_terms: list[str] = Field(default_factory=list, max_length=12)
    native: bool = False
    #: How many pages to ask for. The setup step sends one term per request so
    #: it can show progress, and one page each so that twelve terms stay
    #: twelve requests rather than thirty-six.
    #:
    #: Omitted means **every page the tracker has** (build 105). It used to
    #: mean three, which is how a search the tracker answered with "1-75 out
    #: of 383" showed 75.
    pages: int | None = Field(default=None, ge=1, le=200)
    #: A name the browser made up for this search, so it can ask how far the
    #: search has got while it waits (build 109). Nothing depends on it.
    progress_token: str | None = Field(default=None, max_length=64)
    #: Which term of how many the browser is running, for the same reason.
    term_index: int = Field(default=0, ge=0, le=200)
    term_count: int = Field(default=0, ge=0, le=200)
    #: Which indexer to ask, when the dialog offers a choice (build 219).
    #: Omitted means the default one, as it always did.
    indexer_id: str | None = Field(default=None, max_length=36)


class SearchProgressResponse(BaseModel):
    """How far one search has got. See services/search_progress."""

    term: str = ""
    done: int = 0
    #: Zero while nothing knows yet - a spinner rather than a bar.
    expected: int = 0
    term_index: int = 0
    term_count: int = 0


class NumberingRequest(BaseModel):
    """Which AniDB entry to place in its franchise's numbering."""

    anidb_id: int = Field(ge=1)
    #: Off for "answer from what is already known": the pool and whatever
    #: Shoko holds, with nothing fetched from AniDB.
    fetch: bool = True


class NumberingSeasonResult(BaseModel):
    aid: int
    title: str | None = None
    kind: str | None = None
    episode_count: int | None = None
    absolute_start: int | None = None
    #: True for the entry that was asked about.
    asked: bool = False


class NumberingResponse(BaseModel):
    """Where one season begins, and the chain that says so."""

    anidb_id: int
    #: The franchise number of this season's first episode, when known.
    absolute_start: int | None = None
    absolute_end: int | None = None
    episode_count: int | None = None
    #: pool | shoko | anidb | manual | none
    source: str = "none"
    #: Why there is no answer, when there is none.
    detail: str | None = None
    #: Every entry before this one, oldest first - the arithmetic, shown.
    chain: list[NumberingSeasonResult] = []


class NumberingSaveRequest(BaseModel):
    """A start number a person typed in, which outranks anything fetched."""

    anidb_id: int = Field(ge=1)
    absolute_start: int = Field(ge=1, le=10000)
    episode_count: int | None = Field(default=None, ge=1, le=10000)


class InspectNamesRequest(BaseModel):
    """One show, as much as the caller knows about what it is called.

    The ids matter as much as the titles: AniDB's own title comes from the
    dump by id, and each source's title from the row that source wrote into
    the downloaded calendar.
    """

    title_english: str | None = Field(default=None, max_length=400)
    title_romaji: str | None = Field(default=None, max_length=400)
    title_native: str | None = Field(default=None, max_length=400)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    provider_ids: dict[str, str] = Field(default_factory=dict)


class InspectNameResult(BaseModel):
    kind: str
    value: str
    #: Whether this spelling only exists in the tracker's non-English
    #: category, which searching it has to switch to.
    native: bool = False


class InspectNamesResponse(BaseModel):
    names: list[InspectNameResult]


class InspectedReleaseResult(BaseModel):
    """One hit as it arrived, with everything the parser made of it.

    ``parsed`` carries every field of services/release_parser.ParsedRelease
    rather than a chosen few: the page exists to answer "why is this release
    not showing up", and the answer is usually a field nobody thought to show.
    """

    id: str
    raw_name: str
    size: int | None = None
    seeders: int | None = None
    published_at: str | None = None
    parsed: dict[str, object] | None = None
    #: Why this hit could never reach a row - unreadable, or no title in it.
    skipped: str | None = None
    #: Which search terms turned this up. A release only one narrow term found
    #: is a release the broad term cannot reach - which is the finding.
    found_by: list[str] = []
    #: What a library folder for this release would be called.
    folder_name: str | None = None


class InspectQueryResult(BaseModel):
    """What one term achieved. See services/discovery.InspectQuery."""

    term: str
    #: Rows the indexer handed back, duplicates included, and how many of them
    #: were releases nothing had returned before. A term whose ``new`` is zero
    #: is asking a question already answered.
    found: int = 0
    new: int = 0
    #: How many requests were made for this term.
    pages: int = 0
    #: What the site itself says this search has, when its definition file
    #: says where to read that from. None when it does not, or when the page
    #: could not be read - a missing count is a blank column, never an error.
    site_total: int | None = None
    #: True when the walk stopped because the indexer sent a page it had
    #: already sent, which is what a feed that cannot be paged does.
    repeated: bool = False
    #: Every address this term was asked at, for the page to hand over.
    urls: list[str] = []
    #: How long this term took, in seconds.
    seconds: float = 0.0
    error: str | None = None


class InspectSearchResponse(BaseModel):
    releases: list[InspectedReleaseResult] = []
    queries: list[InspectQueryResult] = []
    failures: list[str] = []
    #: What the indexer's definition file lets a search do, so the page can
    #: say which line is missing rather than look broken (build 108).
    reads_listing: bool = False
    reports_total: bool = False
    definition_file: str | None = None
    #: False when there is no indexer to search at all, which is not the same
    #: as a search that found nothing.
    reached_provider: bool = True


class ConvertAnimeRequest(BaseModel):
    """Turn a tracker entry into one AniFlow can download for.

    The release name is required and everything else is optional: it is the
    one thing no amount of metadata can supply, because it is how release
    groups spell this show rather than how a provider does.
    """

    release_name: str = Field(min_length=1, max_length=255)
    #: The library folder, if the user renamed it in the dialog.
    local_name: str | None = Field(default=None, min_length=1, max_length=180)
    subgroup: str | None = Field(default=None, max_length=100)
    resolution: str | None = Field(default=None, max_length=8)
    #: Which copy of each episode the picked row was: ["uncensored",
    #: "dual-audio"] (build 97). Stored on the profile, so the queue takes the
    #: same copy later that the dialog showed.
    edition: list[str] = Field(default_factory=list, max_length=12)
    #: What the other copies are recognised by, which this one is not
    #: (build 98). Required as well as `edition`: the plain copy carries
    #: everything the dual-audio row asks for minus one tag.
    edition_not: list[str] = Field(default_factory=list, max_length=12)
    template_id: str | None = Field(default=None, max_length=36)
    feed_source_id: str | None = None
    #: Which library it goes into now that it will have files (build 198).
    library_id: int | None = None
    #: The indexer the group was chosen on, kept for the anime's later searches (item 44).
    indexer_id: str | None = Field(default=None, max_length=36)
    #: The user's corrected episode count, if they typed one (build 176).
    episode_count: int | None = Field(default=None, ge=1, le=10000)
    #: What the setup dialog searched and found, as an add sends it (build 208). Without these a
    #: converted entry had a profile and no releases: nothing was written onto its episodes.
    search_query: str | None = Field(default=None, max_length=255)
    scan_ids: list[str] = Field(default_factory=list, max_length=8)
    #: How far this group numbers past its own episode 1.
    episode_offset: int = Field(default=0, ge=0, le=10000)


class UpdateAnimeRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    # How releases name this anime; what feed/indexer matching compares against.
    # Editable because a manually typed folder name may not match any release.
    release_name: str | None = Field(default=None, max_length=255)
    # Where episodes are placed inside the media root, and (stage 2, Shoko Server)
    # inside the import root. Editable because a library gets reorganised: the
    # folder AniFlow was told about at add time is not necessarily the folder it
    # should use a year later.
    media_folder: str | None = Field(default=None, max_length=255)
    import_folder: str | None = Field(default=None, max_length=255)
    lifecycle: AnimeLifecycle | None = None
    drop_mode: str | None = Field(default=None, pattern="^(STOP|FINISH_EXISTING)$")
    automation_enabled: bool | None = None
    #: Mark it adult, or not (build 192).
    is_adult: bool | None = None
    #: Stop looking for releases of this one at all (build 123). Distinct
    #: from automation, which keeps watching and stops downloading.
    releases_ignored: bool | None = None
    feed_source_id: str | None = None
    custom_feed_url: str | None = Field(default=None, max_length=500)
    #: The season the user says it belongs to (item 17): both parts together, picked from lists in the Edit
    #: window so nothing can be misspelt. It is then the user's own and the calendar stops overwriting it.
    season: str | None = Field(default=None, pattern="^(WINTER|SPRING|SUMMER|FALL)$")
    season_year: int | None = Field(default=None, ge=1950, le=2100)
    #: Give the season back to the calendar.
    reset_season: bool = False

    # The airing slot and the episode count the *user* asserts, for a show the
    # provider has wrong or does not know. Sent together: a weekday without an
    # hour is not a slot, and asking for a default of 00:00 would be inventing
    # an answer. The two reset flags clear an override and hand the anime back
    # to whatever the provider says - which may be nothing.
    airs_weekday: int | None = Field(default=None, ge=0, le=6)
    airs_hour: int | None = Field(default=None, ge=0, le=23)
    reset_schedule: bool = False
    episode_count: int | None = Field(default=None, ge=1, le=10000)
    reset_episode_count: bool = False

    # Facts about the show the user can fill in when no provider knows them
    # (build 152) - the Edit dialog puts a field beside every gap the Metadata
    # check reports. Kept from then on: services/metadata_fill.py leaves a
    # value the user typed alone. An empty string clears it again.
    title_romaji: str | None = Field(default=None, max_length=255)
    title_english: str | None = Field(default=None, max_length=255)
    title_native: str | None = Field(default=None, max_length=255)
    format: str | None = Field(default=None, max_length=32)
    air_start_date: date | None = None
    #: AniDB's id, typed in when the title dump could not settle on one. The
    #: names come from the dump once the id is known.
    anidb_id: int | None = Field(default=None, ge=1, le=10_000_000)
    #: The three facts no provider may know about an old show (build 156). An
    #: empty list or an empty synopsis clears them again.
    genres: list[str] | None = Field(default=None, max_length=30)
    studios: list[str] | None = Field(default=None, max_length=10)
    synopsis: str | None = Field(default=None, max_length=5000)

    @field_validator("media_folder", "import_folder")
    @classmethod
    def _plain_folder_name(cls, value: str | None) -> str | None:
        """A folder name, not a path.

        These are joined onto a configured root, so anything that could climb
        out of it - a separator, a parent reference, a drive letter, an absolute
        path - is refused here rather than being sanitised into something the
        user did not ask for.
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return ""  # explicit clear; the route decides what that means
        if any(separator in cleaned for separator in ("/", "\\")):
            raise ValueError("Folder must be a single name, not a path")
        if cleaned in {".", ".."} or cleaned.startswith(".."):
            raise ValueError("Folder must not point outside its root")
        if ":" in cleaned:
            raise ValueError("Folder must not contain a drive letter")
        return cleaned


class AnimeSummary(BaseModel):
    id: str
    primary_provider: str
    primary_provider_id: str
    local_name: str
    display_name: str
    cover_url: str | None
    lifecycle: AnimeLifecycle
    automation_enabled: bool = False
    #: The library it lives in (build 192).
    library_id: int | None = None
    #: Adult, for the watchlist's hide button: the flag, or a library marked
    #: Adult (build 198). Read through an alias like the episode count below,
    #: so an ORM row gives the effective value and a plain dict still works.
    is_adult: bool = Field(default=False, validation_alias=AliasChoices("effective_is_adult", "is_adult"))
    #: The user's own flag alone, for the tick box on the anime's page: the line
    #: above also counts a library marked Adult, which that box cannot change.
    adult_flag: bool = False
    #: The effective count - the user's own when they set one, otherwise the
    #: provider's. Read through an alias so an ORM row gives the effective
    #: value while the detail serializer's plain dict still works.
    episode_count: int | None = Field(
        default=None, validation_alias=AliasChoices("effective_episode_count", "episode_count")
    )
    owned_episodes: int = 0
    available_episodes: int = 0
    #: How many episodes AniFlow brought into the library since the
    #: watchlist was last looked at - the blue corner on the card (build
    #: 151). See ``_new_episodes`` in api/routes/anime.py.
    new_episode_count: int = 0
    #: When the latest of those arrived: the card says "today", and leaving
    #: the watchlist sends the latest one shown back as "seen up to here".
    newest_episode_at: datetime | None = None
    #: How far this anime's group numbers past its own episode 1 (build 113).
    #: The detail page shows both numbers - "#1 (67)" - because the file on
    #: the tracker is called the second one.
    episode_offset: int = 0
    #: The user has asked for no release hunting at all (build 123). Here, not
    #: only on the detail, because the answer to the switch's own PATCH is a
    #: summary: without it the checkbox kept its old value until a second
    #: request came back, and looked as if it could not be ticked (item 61).
    releases_ignored: bool = False
    #: The indexer this anime was chosen from (build 219); null follows the default. Sent so the
    #: Choose release dialog opens on it instead of on the default (item 44).
    indexer_id: str | None = None
    feed_source_id: str | None = None
    custom_feed_url: str | None = None
    created_at: datetime
    #: When the show premiered, as the provider reports it.
    air_start_date: date | None = None
    #: The season the providers place it in (build 190); null until the
    #: calendar says. See services/anime_season.py.
    season: str | None = None
    season_year: int | None = None
    #: The weekly slot, derived in the display timezone: 0 = Monday.
    airs_weekday: int | None = None
    #: The airing time as HH:MM, when the provider gives one.
    airs_time: str | None = None
    #: Started, not finished, and no new episode for three weeks: between parts
    #: (build 205, services/season_state.py). Computed, never stored.
    waiting_for_next_part: bool = False
    #: The user said "keep it in this library": the season rollover skips it.
    library_pinned: bool = False
    #: True when the user uploaded their own artwork for this anime.
    has_custom_cover: bool = False
    #: "basic" for an anime added as a tracker entry, with no release name and
    #: nothing to match releases against. The watchlist says so instead of
    #: showing an episode count it does not have.
    entry_mode: str = "advanced"
    #: AniDB's id for this show, when it is known (build 116). The one id
    #: Shoko Server and the numbering resolver both speak.
    anidb_id: int | None = None
    #: AniDB's own names by language - ``main``, ``en``, ``ja`` and whatever
    #: else the dump carries (build 116). Shown rather than guessed at: for a
    #: sequel these routinely differ from the provider's titles, and this set
    #: is the one that agrees with Shoko and with the trackers.
    anidb_titles: dict[str, str] = Field(default_factory=dict)
    #: Every other name AniFlow holds for it - English, romaji, native, aliases,
    #: AniDB's - for the watchlist's search box (build 208). The card says one
    #: of them; which one you remember is a coin toss.
    search_names: list[str] = Field(default_factory=list)
    #: The genres in metadata_json, for the watchlist's taste page (build 208).
    genres: list[str] = Field(default_factory=list)
    #: TV, MOVIE, OVA ...: the card says it beside the state (build 208).
    format: str | None = None
    #: One colour per group - metadata, download, storage, shoko - as
    #: ``{"metadata": {"colour": "green", "missing": [...]}, ...}`` (120).
    #:
    #: Four groups rather than two halves, and four colours rather than
    #: three: grey means "not applicable here", which is what keeps a basic
    #: entry from showing a permanent red download icon for a feature it
    #: does not have. See services/anime_health.py.
    health: dict[str, dict] = Field(default_factory=dict)

    # populate_by_name so the field can still be filled by its own name from
    # the detail serializer's dict, alias or no alias.
    model_config = {"from_attributes": True, "populate_by_name": True}


class ReleaseInfo(BaseModel):
    """One release a provider published for this episode."""

    id: str
    raw_name: str
    provider: str
    provider_id: str
    has_magnet: bool = False
    magnet_url: str | None = None
    has_torrent: bool = False
    torrent_url: str | None = None
    seeders: int | None = None
    size: int | None = None
    subgroup: str | None = None
    resolution: str | None = None
    version: int = 1

    model_config = {"from_attributes": True}


class DownloadInfo(BaseModel):
    """The torrent that currently represents this episode."""

    id: str
    # The client writes into the download root directly, so there is no
    # per-anime download folder. Kept explicit so the stage reads uniformly.
    folder: str | None = None
    filename: str | None = None
    status: str
    progress: float | None = None
    qb_state: str | None = None
    info_hash: str | None = None
    error: str | None = None
    attention_reason: str | None = None
    label: str | None = None
    version: int = 1


class ImportInfo(BaseModel):
    """The hand-over stage. All three stay null outside Shoko Server mode.

    ``filename`` is what AniFlow put in the import folder - the release's own
    name - and it is cleared once Shoko has filed the file, so a non-null
    value means "still waiting". ``started_at`` says since when, which is how
    the screen tells a normal wait from something nobody is working on.
    """

    folder: str | None = None
    filename: str | None = None
    started_at: datetime | None = None


class MediaInfo(BaseModel):
    """The canonical media file. ``folder`` lives on the anime, not here."""

    filename: str | None = None
    size_bytes: int | None = None
    imported_at: datetime | None = None
    version: int | None = None
    #: Whether the media server can play it as it is (build 87):
    #: direct | transcode | unknown. Null means it has not been read yet.
    playback: str | None = None
    #: "1080p · H.264 High 10 · 10-bit · FLAC", assembled by the backend
    #: because it draws on four different parts of the file.
    summary: str | None = None
    #: Why it would transcode, as codes the interface translates: ten_bit,
    #: burn_in, audio_transcode, high_level, codec_may_transcode.
    findings: list[str] = []
    scanned_at: datetime | None = None


class EpisodeAirDate(BaseModel):
    """When an episode came out or is due, and how that is known (build 216)."""

    #: ``YYYY-MM-DD``.
    on: str
    #: ``recorded`` (stored for this episode), ``exact`` (the premiere), ``from_premiere`` / ``from_anchor`` (counted weekly: an estimate).
    basis: str


class EpisodeStateView(BaseModel):
    """What a person reads about one episode (build 125).

    Derived on read by services/episode_view.py and never stored. It exists
    because the interface had two vocabularies for one question - the
    episode's nine statuses and the download's seven - and chose between them
    per row.
    """

    #: One of services/episode_view.py:STATES.
    state: str
    #: Where or why: a reason code, a raw qBittorrent state, or free text.
    detail: str | None = None
    progress: float | None = None
    actionable: bool = False


class EpisodeCopy(BaseModel):
    """A file next to an episode's own: a backup, a kept original, an older version (189)."""

    id: str
    #: renamed | kept_original | previous_version | awaiting_replacement
    kind: str
    filename: str
    size_bytes: int | None = None
    subgroup: str | None = None
    resolution: str | None = None
    recorded_at: datetime | None = None


class EpisodeSummary(BaseModel):
    """One episode with everything that belongs to it nested underneath."""

    id: str
    number: int
    title: str | None = None
    episode_type: EpisodeType
    #: The internal value. Kept because the Downloads page and the debug views
    #: show it; the interface reads `view` instead.
    status: EpisodeStatus
    view: EpisodeStateView | None = None
    aired_at: datetime | None = None
    #: The day it came out or is due, and whether that is stored or counted weekly (build 216).
    air_date: EpisodeAirDate | None = None
    error: str | None = None
    #: Why nothing is fetched for it although it has no file here (item 36b), or null.
    skip_reason: str | None = None
    releases: list[ReleaseInfo] = []
    download: DownloadInfo | None = None
    # "import" is a reserved word in Python; the JSON field keeps the plain name.
    import_stage: ImportInfo = Field(default_factory=ImportInfo, alias="import")
    media: MediaInfo = Field(default_factory=MediaInfo)
    copies: list[EpisodeCopy] = []

    model_config = {"from_attributes": True, "populate_by_name": True}


class StorageInfo(BaseModel):
    """The three storage roots AniFlow is operating with."""

    download_root: str
    import_root: str | None = None
    media_root: str


class ProfileInfo(BaseModel):
    subgroups: list[str] = []
    resolution: str | None = None
    media_source: str | None = None
    censor_preference: str = "ANY"
    require_title_match: bool = True
    # Which template the profile was copied from. Declared here because the
    # response model filters the payload: without these two the serializer's
    # values were dropped and the detail page called every anime "Custom".
    template_id: str | None = None
    template_name: str | None = None

    model_config = {"from_attributes": True}


class AnimeDetail(AnimeSummary):
    #: Other databases' ids for it (build 237) - the MAL id and its Open MAL button.
    provider_ids: dict[str, str] = Field(default_factory=dict)
    #: Genres, studios and the synopsis, for the Edit dialog (build 156).
    genres: list[str] = Field(default_factory=list)
    studios: list[str] = Field(default_factory=list)
    synopsis: str | None = None
    #: True when the airing slot or the episode count on screen is the user's
    #: own rather than the provider's. The detail page needs to know, because
    #: "reset" has nothing to do when nothing was overridden.
    schedule_is_manual: bool = False
    episode_count_is_manual: bool = False
    #: The season on screen is the user's own (item 17), so Reset has something to drop.
    season_is_manual: bool = False
    primary_provider: str
    primary_provider_id: str
    title_english: str | None
    title_romaji: str | None
    title_native: str | None
    aliases: list[str]
    format: str | None
    local_name: str
    drop_mode: str | None
    release_name: str | None = None
    media_folder: str | None = None
    import_folder: str | None = None
    feed_source_name: str | None = None
    profile: ProfileInfo | None = None
    storage: StorageInfo | None = None
    episodes: list[EpisodeSummary]

class FeedSearchRequest(BaseModel):
    feed_source_id: str | None = None
    url: str | None = Field(default=None, max_length=1000, pattern="^https?://")


class QueueReleaseRequest(BaseModel):
    raw_name: str = Field(min_length=1, max_length=500)
    # http(s) torrent file URLs AND magnet links are both accepted.
    torrent_url: str = Field(min_length=1, max_length=2000)

    @field_validator("torrent_url")
    @classmethod
    def _validate_torrent_url(cls, value: str) -> str:
        lowered = value.lower()
        is_http = lowered.startswith("http://") or lowered.startswith("https://")
        is_magnet = lowered.startswith("magnet:?xt=urn:btih:")
        if not (is_http or is_magnet):
            raise ValueError("torrent_url must be an http(s) URL or a magnet link")
        return value
    episode: str | None = Field(default=None, max_length=32)
    version: int = Field(default=1, ge=1, le=99)

class AliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=200)


class ConnectReleaseRequest(BaseModel):
    raw_name: str = Field(min_length=1, max_length=500)


class LinkFolderRequest(BaseModel):
    raw_name: str = Field(min_length=1, max_length=500)
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.core.mode import is_shoko_mode
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.models.anime import Anime, AnimeLifecycle, Episode, EpisodeStatus, EpisodeType
from backend.app.models.profile_template import ProfileTemplate
from backend.app.models.release_profile import CensorPreference, ReleaseProfile
from backend.app.models.rss import FeedSource
from backend.app.schemas.anime import AddAnimeRequest, UpdateAnimeRequest

logger = for_component("Library")


def preferred_folder_name(anime, settings=None) -> str:
    """What to call the folder AniFlow creates for this anime.

    Follows ``title_folder_order``: the first of the anime's titles the user
    asked for that it actually has. Falls back to the display name, so an
    anime missing every preferred title still gets a folder.

    Only used where AniFlow names the folder - the library in standalone mode,
    and the hand-over folder in Shoko Server mode. Shoko's own library naming
    is not AniFlow's to decide.
    """
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.title_preference import (
        DEFAULT_FOLDER_ORDER,
        from_anime,
        parse_order,
        pick,
    )

    resolved = settings if settings is not None else load_app_settings()
    order = parse_order(
        getattr(resolved, "title_folder_order", None), DEFAULT_FOLDER_ORDER
    )
    return normalize_local_name(pick(from_anime(anime), order))


def normalize_local_name(name: str) -> str:
    """Make a stable, portable folder label without treating it as a filesystem path."""
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip().rstrip(".")
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized or normalized in {".", ".."}:
        raise ValueError("Local name does not contain a usable folder name")
    return normalized[:180]


def _file_is_gone(anime: Anime, episode) -> bool:
    """Whether an episode's recorded file is verifiably not on disk (build 266).

    Verifiably: the folder it should be in is there and the file is not. A folder that cannot be
    reached at all - an unmounted share - proves nothing, and an outage must never empty the list.
    """
    from backend.app.services.storage import media_path, roots_for, storage_roots

    target = media_path(roots_for(anime, storage_roots()), anime.media_folder, episode.media_filename)
    return target is not None and target.parent.exists() and not target.exists()


def trim_episode_rows(db: Session, anime: Anime, episode_count: int | None) -> int:
    """Remove the empty episode rows past the count (build 190).

    The other half of ``ensure_episode_rows``. Reported with a provider that
    said thirteen for a twelve-episode show: the user corrected it to twelve,
    the box showed twelve, and the page still listed an episode 13 and warned
    that it had nothing to download. Rows were only ever added.

    Only rows that hold nothing go: regular episodes past the count with no
    file, no download, nothing placed or waiting. An episode that has a file
    is never removed because a number changed - the count is a claim, the
    file is a fact. A recorded file that is verifiably not on disk is not a fact
    any more (build 266): a Shoko import that guessed 17 episodes for a 5-episode
    folder left twelve rows pointing at nothing, and lowering the count to 5 did
    not clear them.

    Returns how many rows were removed.
    """
    if not episode_count or episode_count < 1:
        return 0
    removed = 0
    for episode in list(anime.episodes):
        if episode.episode_type is not EpisodeType.REGULAR or episode.number <= episode_count:
            continue
        if episode.media_filename and not _file_is_gone(anime, episode):
            continue
        if (
            episode.import_filename
            or episode.download_filename
            or episode.downloads
            or episode.copies
        ):
            continue
        if episode.status in (EpisodeStatus.DOWNLOADING, EpisodeStatus.DOWNLOADED, EpisodeStatus.IMPORTING):
            continue
        if episode.status is EpisodeStatus.COMPLETE and not episode.media_filename:
            continue
        db.delete(episode)
        removed += 1
    if removed:
        db.flush()
        db.refresh(anime)
    return removed


def trim_surplus_in(session_factory) -> int:
    """Trim every anime whose count was set by hand below its rows (build 190).

    Catches the ones corrected before trimming existed. Only anime with the
    user's own count are looked at, so this is a short list.
    """
    session = session_factory()
    try:
        trimmed = 0
        for anime in session.scalars(select(Anime).where(Anime.episode_count_override.is_not(None))):
            trimmed += trim_episode_rows(session, anime, anime.effective_episode_count)
        if trimmed:
            session.commit()
        return trimmed
    finally:
        session.close()


def ensure_episode_rows(
    db: Session,
    anime: Anime,
    episode_count: int | None,
    *,
    include_zero: bool = False,
) -> int:
    """Make sure a row exists for every episode AniFlow expects.

    Knowing an anime has twelve episodes means twelve rows exist immediately,
    even though almost everything about them is unknown - the episode list is
    the UI's spine, so it must not depend on what has been discovered yet.
    Unknown fields stay NULL; nothing is invented.

    Episode 0 (a prologue/special counted in-line) is supported and simply
    sorts first. It is only created when explicitly requested, because
    providers report a count that starts at 1.

    Returns the number of rows created. Safe to call repeatedly.
    """
    if not episode_count or episode_count < 1:
        return 0
    existing = {
        episode.number
        for episode in db.scalars(
            select(Episode).where(
                Episode.anime_id == anime.id, Episode.episode_type == EpisodeType.REGULAR
            )
        )
    }
    first = 0 if include_zero else 1
    created = 0
    for number in range(first, episode_count + 1):
        if number in existing:
            continue
        db.add(
            Episode(
                anime=anime,
                number=number,
                episode_type=EpisodeType.REGULAR,
                status=EpisodeStatus.UNAVAILABLE,
            )
        )
        created += 1
    return created


from backend.app.services.adult_rule import ADULT_GENRES  # noqa: E402,F401 - one rule (build 198)


def add_anime(db: Session, request: AddAnimeRequest, candidate: AnimeCandidate) -> Anime:
    from backend.app.services.metadata_fill import merged_metadata

    existing = db.scalar(
        select(Anime).where(
            Anime.primary_provider == request.provider,
            Anime.primary_provider_id == request.provider_id,
        )
    )
    if existing:
        return existing

    local_name = normalize_local_name(request.local_name)
    # The release name is what matching compares against; the folder is only
    # where files land. They start out the same, but the user may rename the
    # folder in the setup step without changing what releases are called.
    release_name = (request.release_name or "").strip() or local_name
    anime = Anime(
        primary_provider=candidate.provider,
        primary_provider_id=candidate.provider_id,
        local_name=local_name,
        display_name=request.display_name,
        title_english=candidate.title_english,
        title_romaji=candidate.title_romaji,
        title_native=candidate.title_native,
        aliases=list(candidate.aliases),
        cover_url=candidate.cover_url,
        format=candidate.format,
        episode_count=candidate.episode_count,
        lifecycle=request.lifecycle,
        provider_ids=candidate.provider_ids,
        # Normalised beside the raw payload (build 123). Every source sends
        # genres, studios and a synopsis in its own shape - AniList nests
        # studios under `nodes`, and calls the synopsis `description` - so
        # anything reading the column had to know which source it came from,
        # and services/anime_health.py only knew AniList's. The candidate has
        # already normalised them; this writes those too. See
        # services/metadata_fill.py.
        metadata_json=merged_metadata({}, candidate),
        # The name providers use for this show - the result the user selected,
        # not the (renameable) library folder.
        release_name=release_name,
        # Stored, not derived: a later naming stage may move the library folder
        # without renaming the anime, and files already on disk must keep
        # resolving. Starts out equal to the local name - except in Shoko
        # Server mode, where Shoko names the folder and AniFlow has no idea
        # what it will be. Inventing one there is worse than having none: the
        # anime would point at a folder that never exists, and every episode
        # found in Shoko's real folder would read as MISSING.
        media_folder=None if is_shoko_mode() else local_name,
        # Adding an anime to the watchlist is the decision to have AniFlow look
        # after it, so automation starts on - unless the user's Settings say otherwise (build 239).
        # Set here rather than as the column's default, so this is the only path that turns it on
        # and anime already in the database keep whatever they had.
        automation_enabled=_automate_new_anime(),
        # When it airs, from the provider. Kept so the weekly schedule works
        # offline and does not re-query for every render.
        air_start_date=candidate.air_start_date,
        air_slot_at=candidate.air_slot_at,
    )
    if request.episode_count and request.episode_count != candidate.episode_count:
        # The user corrected the provider's count while adding (build 176):
        # kept as their own, so a metadata refresh cannot undo it.
        anime.episode_count_override = request.episode_count
    # Nothing enters the watchlist without a library (build 192): the one
    # asked for, or the default.
    from backend.app.models.library import Library
    from backend.app.services import libraries

    chosen = db.get(Library, request.library_id) if request.library_id else None
    anime.library_id = (chosen or libraries.default_library(db)).id
    # Adult starts from what the source says - its own flag, or an explicit
    # genre - and is the user's to change from then on.
    anime.is_adult = bool(candidate.is_adult) or any(
        str(genre).strip().casefold() in ADULT_GENRES for genre in candidate.genres or ()
    )
    # The site the release came from (build 219). Kept so later searches ask
    # it, and so an indexer that lists only adult releases (Sukebei) marks
    # what is added from it - the user can still untick it afterwards.
    apply_indexer(db, anime, getattr(request, "indexer_id", None))
    db.add(anime)

    # Whether its first episode exists yet (build 121). Asked of the date
    # rather than of the lifecycle, which providers update late and often -
    # and taken from the request when the dialog has already decided, so a
    # show with no date at all can still be added as a future release.
    unaired = bool(getattr(request, "future", False)) or _not_aired_yet(candidate)

    # AniDB's id and AniDB's own names, while the decision is being made
    # (build 116). Both matter later and neither can be worked out later as
    # reliably as it can be now: the dialog has already established which
    # AniDB entry this season is, and by the time Shoko or the numbering
    # resolver wants the id, all that is left to match on is a folder name.
    #
    # Best effort. The dump is optional - it is a 1.4 MB file AniFlow fetches
    # weekly and a fresh install has not got yet - and an anime with no AniDB
    # names is the state every anime was in until this build.
    try:
        from backend.app.services.anidb_identity import adopt

        adopt(anime, request.anidb_id)
    except Exception as exc:  # noqa: BLE001 - never block an add over names
        logger.info("no AniDB names for %s yet: %s", local_name, exc)

    basic = _basic_entry(db, anime, unaired=unaired)
    # A future release has no files, so it waits in Upcoming (a library with no
    # folder) unless one was asked for; a real entry needs a folder, so it is
    # never left in Upcoming.
    upcoming = libraries.upcoming_library(db)
    if anime.entry_mode == "future" and chosen is None:
        anime.library_id = upcoming.id
    elif anime.entry_mode != "future" and anime.library_id == upcoming.id:
        anime.library_id = libraries.default_library(db).id
    if anime.entry_mode == "future":
        from backend.app.services.lifecycle_auto import mark_upcoming

        mark_upcoming(anime)
    if basic and anime.entry_mode == "future":
        # A future release, which is not the same as a tracker entry.
        #
        # No release name, no profile, no feed source and no automation:
        # there is nothing to match and nothing to fetch. But the episode
        # rows *are* created, which a basic entry deliberately does not do -
        # for an unaired show the count is a fact about the show, the rows
        # are all UNAVAILABLE and honest, and the detail page shows the
        # season somebody is waiting for rather than an empty panel.
        anime.release_name = None
        anime.media_folder = None
        anime.automation_enabled = False
        ensure_episode_rows(db, anime, anime.effective_episode_count)
        db.commit()
        db.refresh(anime)
        logger.info(
            "%s added as a future release - premieres %s",
            anime.display_name, candidate.air_start_date,
        )
        return anime
    if basic:
        # A tracker entry, because there is no indexer to download with.
        #
        # No release name: it is what matching compares against, and a name
        # nothing was matched to would be a claim AniFlow cannot back up - the
        # user never chose it from a list of real releases. No profile and no
        # feed source either, for the same reason.
        #
        # The episode rows *are* created, since build 138. They used to be left
        # out because "rows with no releases behind them would only be counted
        # and reported as missing" - which is no longer true at either end:
        # anime_health greys the download group out for a basic entry and
        # judges storage, *"because the files may well be there"*, and the
        # detail screen lists the episodes for the same reason. How many
        # episodes a show has is a fact about the show; it was being withheld
        # for a reason that belonged to downloading.
        #
        # Everything the provider knows is still stored, so a conversion later
        # has all of it and asks the user only for the part nothing else can
        # supply: which releases are this show.
        anime.release_name = None
        anime.media_folder = None
    # Every entry gets its episode rows, whatever it can do with them.
    ensure_episode_rows(db, anime, anime.effective_episode_count)
    if not basic:
        _apply_new_anime_defaults(
            db,
            anime,
            subgroup=request.subgroup,
            resolution=request.resolution,
            edition=request.edition,
            edition_not=request.edition_not,
            template_id=request.template_id,
            feed_source_id=request.feed_source_id,
        )
    db.commit()
    db.refresh(anime)

    # The folder is created right away so it's ready for imports as soon as
    # the anime is added - this mirrors link_release_folder's own explicit
    # "user just confirmed this" creation, just at add-time instead of later.
    #
    # Never in Shoko Server mode: that directory belongs to Shoko, which names
    # its own folders. One created here would be empty, named the way AniFlow
    # would have named it, and scanned by Shoko and Jellyfin as though it meant
    # something.
    if not is_shoko_mode() and not basic:
        from backend.app.services.library import ensure_anime_folder

        ensure_anime_folder(anime)

    # Persist the indexer hits that were previewed for this folder so the
    # episode list can show "Available" (orange) with filename + magnet ready
    # for the future download manager.
    search_q = (request.search_query or "").strip() or candidate.title_romaji or candidate.title_english or candidate.display_name
    # What the setup dialog decided about this group's numbering, before the
    # releases are written down - persist_available_from_preview maps every
    # label through it.
    offset = int(getattr(request, "episode_offset", 0) or 0)
    if offset:
        anime.episode_offset = offset
        db.flush()
        logger.info(
            "%s: this group counts from %s, so its files are %s ahead",
            anime.display_name, offset + 1, offset,
        )

    if search_q:
        try:
            from backend.app.services import scan_store
            from backend.app.services.discovery import persist_available_from_preview

            persist_available_from_preview(
                db,
                anime,
                search_query=search_q,
                # Availability is matched by release name, never by the folder
                # the user may have renamed.
                selected_folder=release_name,
                subgroup=request.subgroup,
                resolution=request.resolution,
                edition=request.edition,
                edition_not=request.edition_not,
                # What the dialog already found, if its tickets are still
                # good. None means "search again", the old behaviour.
                known=scan_store.recall_all(getattr(request, "scan_ids", None)),
            )
        except Exception as exc:  # noqa: BLE001 - never block an add
            # Best-effort, not silent. An indexer that times out or answers
            # badly used to leave no trace, so the anime arrived with no
            # releases and nothing anywhere said why - and a line in a log
            # nobody reads is still nothing, so it is also a record on the
            # Activity page (build 231).
            record_lookup_failure(db, anime, exc, when="after adding it")
    return anime


def release_count(anime: Anime) -> int:
    """How many releases are attached to this anime's episodes."""
    return sum(len(episode.releases) for episode in anime.episodes)


def record_lookup_failure(db: Session, anime: Anime, exc: Exception, *, when: str) -> str:
    """Say that the release lookup did not finish: in the log and on the Activity page. Returns the reason."""
    from backend.app.services.attention import log_activity

    reason = f"{type(exc).__name__}: {exc}"[:200]
    logger.warning("%s: the release lookup %s did not finish (%s)", anime.display_name, when, reason)
    try:
        # What the lookup half-did is not worth keeping; the anime itself was committed before it began.
        db.rollback()
        log_activity(
            db,
            kind="RELEASE_LOOKUP_FAILED",
            message=f"{anime.display_name}: the release lookup {when} did not finish: {reason}",
            details={"reason": reason, "when": when},
            anime_id=anime.id,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - a record must never be the reason for a second error
        db.rollback()
    return reason


def look_for_releases_now(db: Session, anime: Anime) -> tuple[int, int, str | None]:
    """The lookup an add does, on request: search the indexer for this anime's release and attach what it finds.

    For the anime that arrived with nothing attached (build 231): the page had "Choose release" and nothing
    else, so there was no way to ask again. Returns (found, attached to episodes now, the reason it failed).
    """
    from backend.app.services.discovery import persist_available_from_preview

    profile = anime.profile
    query = (
        (anime.release_name or "").strip()
        or anime.title_romaji
        or anime.title_english
        or anime.display_name
    )
    if not query:
        return 0, release_count(anime), "this anime has no name to search for"
    try:
        found = persist_available_from_preview(
            db,
            anime,
            search_query=query,
            selected_folder=anime.release_name or query,
            subgroup=(profile.subgroups[0] if profile and profile.subgroups else None),
            resolution=profile.resolution if profile else None,
            edition=(list(profile.edition) or None) if profile else None,
            edition_not=(list(profile.edition_not) or None) if profile else None,
        )
        db.commit()
        db.refresh(anime)
    except Exception as exc:  # noqa: BLE001 - shown to the person who asked, not raised
        return 0, release_count(anime), record_lookup_failure(db, anime, exc, when="you asked for")
    return int(found or 0), release_count(anime), None


def _default_template(db: Session) -> ProfileTemplate | None:
    """The template to use when the caller expressed no preference.

    "The only one that exists" counts as the default, so a single-template
    setup works without anyone marking it.
    """
    templates = list(db.scalars(select(ProfileTemplate)))
    default_template = next((t for t in templates if t.is_default), None)
    if default_template is None and len(templates) == 1:
        default_template = templates[0]
    return default_template


def _profile_from_template(
    anime: Anime,
    template: ProfileTemplate,
    *,
    subgroup: str | None,
    resolution: str | None,
) -> ReleaseProfile:
    """Copy a template onto an anime, honouring explicit overrides.

    The whole template is copied, not just subgroup and resolution: media
    source, censorship preference and the require-known switches are part of
    what the user configured, and dropping them silently produced a profile
    that did not match the template it claimed to come from.

    Copy semantics on purpose (AGENTS.md): later edits to the template never
    change an already configured anime. ``template_id`` records the origin so
    the UI can show it.
    """
    return ReleaseProfile(
        anime_id=anime.id,
        # An explicit pick wins - the dialog offers a single subgroup out of a
        # template that allows several.
        subgroups=[subgroup] if subgroup else list(template.subgroups),
        resolution=resolution or template.resolution,
        media_source=template.media_source,
        censor_preference=template.censor_preference,
        require_title_match=template.require_title_match,
        require_resolution_known=template.require_resolution_known,
        require_source_known=template.require_source_known,
        require_censor_known=template.require_censor_known,
        template_id=template.id,
    )


def _not_aired_yet(candidate) -> bool:
    """Whether this show's first episode is still ahead of us (build 121).

    The premiere date decides. A lifecycle of "UPCOMING" is not used: a
    provider updates that late and often, and a show that started on Tuesday
    is airing whatever the record still says.

    No date at all is *not* treated as unaired - plenty of finished shows
    have no premiere recorded, and calling those future releases would hide
    them from the only part of the watchlist that downloads anything.
    """
    from datetime import date

    started = getattr(candidate, "air_start_date", None)
    return bool(started and started > date.today())


def _basic_entry(db, anime, *, unaired: bool = False) -> bool:
    """Whether this anime is being added without any download configuration.

    Two situations answer yes, and the row records which:

    ``basic``
        In Basic mode there is no indexer, so there is nothing to match
        releases against and no profile worth writing. The anime gets a name,
        a lifecycle and the provider's own metadata - which is exactly what a
        watchlist is.
    ``future``
        The show's first episode has not aired (build 121). Same absence of
        download configuration, for a completely different reason - and one
        that ends on a known date, when the entry is offered for conversion.
    """
    from backend.app.services.app_mode import EntryMode, entry_mode_for

    mode = entry_mode_for(db, unaired=unaired)
    anime.entry_mode = mode.value
    return mode in (EntryMode.BASIC, EntryMode.FUTURE)


def _replace_profile(db: Session, anime: Anime, profile: ReleaseProfile) -> None:
    """Give the anime this profile in place of any it already has.

    A profile row is unique per anime, and SQLAlchemy inserts the new row
    before it deletes the one being replaced - so assigning over an existing
    profile failed with a UNIQUE error and the request answered 500. That is
    what "Choose release" did the second time it was pressed on an entry that
    had already been converted (build 188). The old row is removed and the
    removal flushed first.
    """
    old = anime.profile
    if old is not None:
        anime.profile = None
        db.delete(old)
        db.flush()
    anime.profile = profile
    db.add(profile)


def _apply_new_anime_defaults(
    db: Session,
    anime: Anime,
    *,
    subgroup: str | None = None,
    resolution: str | None = None,
    edition: list[str] | None = None,
    edition_not: list[str] | None = None,
    template_id: str | None = None,
    feed_source_id: str | None = None,
) -> None:
    """Apply the anime's release profile and feed source.

    A chosen template is copied in full and stays linked. Hand-typed values
    with no template behind them produce an unlinked ("custom") profile. With
    no choice at all, the default template and feed source apply so automation
    still works for callers that never went through the setup step.
    """
    # Precedence: an explicitly chosen template, then hand-typed values (which
    # are genuinely custom and must not be linked to some default), then the
    # default template for callers that chose nothing at all.
    if template_id:
        template = db.get(ProfileTemplate, template_id)
    elif subgroup or resolution or edition or edition_not:
        template = None
    else:
        template = _default_template(db)

    if template is not None:
        profile = _profile_from_template(
            anime, template, subgroup=subgroup, resolution=resolution
        )
        # The picked edition wins over the template's, the way the picked
        # group and quality do: it names one of the copies that were actually
        # on offer a moment ago.
        if edition:
            profile.edition = list(edition)
        if edition_not:
            profile.edition_not = list(edition_not)
        _replace_profile(db, anime, profile)
    elif subgroup or resolution or edition or edition_not:
        # Genuinely custom: no template to point at.
        _replace_profile(
            db,
            anime,
            ReleaseProfile(
                anime_id=anime.id,
                subgroups=[subgroup] if subgroup else [],
                resolution=resolution,
                edition=list(edition or []),
                edition_not=list(edition_not or []),
                media_source=None,
                censor_preference=CensorPreference.ANY,
                require_title_match=True,
                require_resolution_known=False,
                require_source_known=False,
                require_censor_known=False,
                template_id=None,
            ),
        )

    if feed_source_id is not None:
        anime.feed_source_id = feed_source_id
    else:
        sources = list(db.scalars(select(FeedSource)))
        default_source = next((s for s in sources if s.is_default), None)
        if default_source is None and len(sources) == 1:
            default_source = sources[0]
        if default_source is not None:
            anime.feed_source_id = default_source.id


def forget_release_warnings(db: Session, anime: Anime) -> int:
    """Close the warnings that were about finding releases (build 123).

    Switching the ignore flag on makes each of these a complaint about a
    decision the user has just made: nothing is looking for releases any
    more, so "nothing was found" and "this needs its releases set up" have
    stopped being problems rather than been solved.

    RESOLVED rather than deleted, as everywhere: the history still says what
    happened, and switching the flag back off leaves the next pass to raise
    whatever is still true.
    """
    from backend.app.models.activity import AttentionKind
    from backend.app.services.attention import resolve_open

    closed = 0
    for kind in (
        AttentionKind.NO_RELEASES,
        AttentionKind.DOWNLOAD_STALLED,
        AttentionKind.NEEDS_CONVERSION,
    ):
        closed += resolve_open(db, kind=kind, anime_id=anime.id)
    return closed


def update_anime(anime: Anime, request: UpdateAnimeRequest) -> Anime:
    if request.display_name is not None:
        anime.display_name = request.display_name
    if request.lifecycle is not None:
        anime.lifecycle = request.lifecycle
        # A person chose this, so the automatic "every episode is here, so it
        # is complete" pass leaves the anime alone from now on (build 188).
        from backend.app.services.lifecycle_auto import mark_manual

        # Upcoming is "has not started": it is not a decision the automatic passes must keep, and it
        # has to turn Active by itself when the show starts (build 207).
        mark_manual(anime, request.lifecycle is not AnimeLifecycle.UPCOMING)
        # A drop mode is meaningful only while dropped. Retain no accidental
        # automation instruction if the anime becomes active/archived again.
        if request.lifecycle is not AnimeLifecycle.DROPPED:
            anime.drop_mode = None
    if request.drop_mode is not None:
        if anime.lifecycle is not AnimeLifecycle.DROPPED:
            raise ValueError("A drop mode can only be set for a dropped anime")
        anime.drop_mode = request.drop_mode
    if request.automation_enabled is not None:
        anime.automation_enabled = request.automation_enabled
    if request.is_adult is not None:
        anime.is_adult = request.is_adult
        # The user's own word: data never overrides it from now on (build 198).
        from backend.app.services.metadata_fill import mark_manual

        mark_manual(anime, "is_adult", True)
    if request.releases_ignored is not None:
        anime.releases_ignored = request.releases_ignored
    _apply_manual_schedule(anime, request)
    _apply_manual_facts(anime, request)
    return anime


def _apply_manual_facts(anime: Anime, request: UpdateAnimeRequest) -> None:
    """Titles, kind, premiere and AniDB id, as the user states them (152).

    Each one written is remembered as the user's own, so the next metadata
    refill leaves it alone - see ``metadata_fill.MANUAL_KEY``. An empty value
    clears the field and hands it back to the providers.
    """
    from backend.app.services.anidb_confirmation import confirm
    from backend.app.services.metadata_fill import mark_manual

    fields = request.model_fields_set
    for column in ("title_romaji", "title_english", "title_native", "format"):
        if column not in fields:
            continue
        value = (getattr(request, column) or "").strip() or None
        setattr(anime, column, value)
        mark_manual(anime, column, value is not None)
    if "air_start_date" in fields:
        anime.air_start_date = request.air_start_date
        mark_manual(anime, "air_start_date", request.air_start_date is not None)
    if request.reset_season:
        # Back to the calendar: emptied here, filled again by the worker's next pass (item 17).
        anime.season = anime.season_year = None
        mark_manual(anime, "season", False)
    elif request.season is not None or request.season_year is not None:
        if request.season is None or request.season_year is None:
            raise ValueError("A season needs both the season and the year")
        anime.season, anime.season_year = request.season, request.season_year
        mark_manual(anime, "season", True)
    if request.anidb_id is not None:
        # confirm() is adopt() - a given id over a stored one and over the
        # dump's guess, AniDB's own names filled in from the dump - plus the
        # record that a person typed this id, so it is no longer a guess.
        confirm(anime, request.anidb_id)
    # Genres, studios and the synopsis live in metadata_json under the keys
    # every reader uses (build 156); marked as the user's like the rest.
    for key in ("genres", "studios", "synopsis"):
        if key not in fields:
            continue
        value = getattr(request, key)
        if isinstance(value, list):
            value = [item.strip() for item in value if item and item.strip()]
        elif isinstance(value, str):
            value = value.strip() or None
        raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
        raw[key] = value or ([] if key != "synopsis" else None)
        anime.metadata_json = raw
        mark_manual(anime, key, bool(value))



def remember_release_identity(
    db: Session,
    anime: Anime,
    *,
    release_name: str | None = None,
    subgroup: str | None = None,
    resolution: str | None = None,
    edition: list[str] | None = None,
    edition_not: list[str] | None = None,
) -> list[str]:
    """Write what this anime is released as. Returns what changed.

    The matching half of a release change, with none of the library half
    (build 123). Nothing is deleted, nothing is renamed, nothing is queued and
    no episode is touched - the only things written are the four values later
    matching is done against.

    The profile is *edited* rather than replaced. An adopted entry may already
    have one with a media source and censorship preference somebody set, and
    the question being answered here is "which group, at what quality", not
    "start again".
    """
    changed: list[str] = []
    wanted = (release_name or "").strip()
    if wanted and wanted != anime.release_name:
        anime.release_name = wanted[:255]
        changed.append("release_name")

    if subgroup or resolution or edition or edition_not:
        if anime.profile is None:
            anime.profile = ReleaseProfile(
                anime_id=anime.id,
                subgroups=[],
                media_source=None,
                censor_preference=CensorPreference.ANY,
                require_title_match=True,
                require_resolution_known=False,
                require_source_known=False,
                require_censor_known=False,
                template_id=None,
            )
            db.add(anime.profile)
        if subgroup and anime.profile.subgroups != [subgroup]:
            anime.profile.subgroups = [subgroup]
            changed.append("subgroup")
        if resolution and anime.profile.resolution != resolution:
            anime.profile.resolution = resolution
            changed.append("resolution")
        if edition:
            anime.profile.edition = list(edition)
            changed.append("edition")
        if edition_not:
            anime.profile.edition_not = list(edition_not)
            changed.append("edition_not")
    return changed

def _file_in_a_library_with_a_folder(db: Session, anime: Anime, library_id: int | None) -> None:
    """A real entry will have files, so it cannot stay in Upcoming (build 198).

    The library asked for, if it has a folder; otherwise the one it is in, if
    that has one; otherwise the default.
    """
    from backend.app.models.library import Library
    from backend.app.services import libraries

    chosen = db.get(Library, library_id) if library_id else None
    if chosen is not None and chosen.has_folder:
        anime.library_id, anime.library = chosen.id, chosen
    elif anime.library is None or not anime.library.has_folder:
        default = libraries.default_library(db)
        anime.library_id, anime.library = default.id, default


def _automate_new_anime() -> bool:
    """The Watchlist setting: does a new anime start with Automate on."""
    from backend.app.services.app_settings import load_app_settings

    try:
        return bool(load_app_settings().automate_new_anime)
    except Exception:  # noqa: BLE001 - a settings read must not stop an add
        return True


def convert_to_advanced(
    db: Session,
    anime: Anime,
    *,
    release_name: str,
    local_name: str | None = None,
    subgroup: str | None = None,
    resolution: str | None = None,
    edition: list[str] | None = None,
    edition_not: list[str] | None = None,
    template_id: str | None = None,
    feed_source_id: str | None = None,
    episode_count: int | None = None,
    library_id: int | None = None,
) -> Anime:
    """Turn a tracker entry into one AniFlow can download for.

    Deliberately not automatic. The release name is the one thing no amount of
    metadata can supply: it is how release groups spell this show, and getting
    it wrong means either downloading nothing or downloading somebody else's
    series. The user picks it from real search results, exactly as they would
    for a new anime - this just applies the answer to a row that already
    exists.
    """
    from backend.app.services.app_mode import EntryMode

    name = (release_name or "").strip()
    if not name:
        raise ValueError("A release name is needed to convert this anime")

    anime.release_name = name
    if episode_count and episode_count != anime.episode_count:
        anime.episode_count_override = episode_count
    if local_name:
        anime.local_name = normalize_local_name(local_name)
    if anime.media_folder is None and not is_shoko_mode():
        anime.media_folder = preferred_folder_name(anime)
    if anime.entry_mode == "future":
        # A future entry was added with automation off - there was nothing to fetch. Choosing its group is
        # what makes it fetchable, so it gets the user's default now (build 239).
        anime.automation_enabled = _automate_new_anime()
    anime.entry_mode = EntryMode.ADVANCED.value
    from backend.app.services.lifecycle_auto import activate_when_it_starts

    activate_when_it_starts(anime)
    _file_in_a_library_with_a_folder(db, anime, library_id)
    _apply_new_anime_defaults(
        db,
        anime,
        subgroup=subgroup,
        resolution=resolution,
        edition=edition,
        edition_not=edition_not,
        template_id=template_id,
        feed_source_id=feed_source_id,
    )
    # Now that there is something to match, the episodes it would match are
    # worth having rows for.
    ensure_episode_rows(db, anime, anime.effective_episode_count)
    return anime


def _apply_manual_schedule(anime: Anime, request: UpdateAnimeRequest) -> None:
    """The user's own airing slot and episode count.

    Written to their own columns, never over the provider's, so a reset is a
    matter of clearing them and a metadata refresh cannot undo a correction.

    A reset wins over a value sent in the same request: the button says "back
    to the provider", and honouring both at once would be neither.
    """
    from backend.app.services.schedule import manual_slot

    if request.reset_schedule:
        anime.air_slot_override_at = None
    elif request.airs_weekday is not None or request.airs_hour is not None:
        if request.airs_weekday is None or request.airs_hour is None:
            raise ValueError("A manual airing slot needs both a weekday and an hour")
        anime.air_slot_override_at = manual_slot(request.airs_weekday, request.airs_hour)

    if request.reset_episode_count:
        anime.episode_count_override = None
    elif request.episode_count is not None:
        anime.episode_count_override = request.episode_count


def apply_indexer(db, anime, indexer_id: str | None) -> None:
    """Remember which indexer this anime's releases come from (build 219, item 44).

    Used by every dialog that picks a release - add, convert, change the release, fix the
    missing episodes - so the choice made in the dropdown is the one later searches ask. An
    id that is not an indexer is ignored, and an indexer that lists only adult releases
    (Sukebei) marks the anime adult upward only; the user can still untick it.
    """
    if not indexer_id:
        return
    from backend.app.models.indexer import Indexer
    from backend.app.services.indexer_definitions import find_for_url

    chosen = db.get(Indexer, indexer_id)
    if chosen is None:
        return
    anime.indexer_id = chosen.id
    definition = find_for_url(chosen.url)
    if definition is not None and definition.adult:
        anime.is_adult = True

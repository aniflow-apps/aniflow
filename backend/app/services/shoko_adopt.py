"""Taking Shoko's library onto the watchlist, whole (build 117).

Until this build the two libraries were two lists with a filter between them:
the watchlist, and a "Only in Shoko" section of cards that looked different,
behaved differently and had to be adopted one at a time. Asked for directly -
*"the 'Only in Shoko' category will be gone"*, and a Shoko series *"should try
to add ALL information available"*.

So this is the whole crossing, on the server, where the information is:

**Adoption.** Every series Shoko holds that the watchlist does not becomes a
watchlist entry - from the downloaded season line-up when it knows the show,
and otherwise from AniDB's own titles and Shoko's episode count (build 118),
which is what makes a library of finished older shows adoptable at all. Deliberately without the indexer: adopting two hundred series
must not fire two hundred searches at a tracker, and the entry is useful the
moment it exists. Its releases are found the ordinary way afterwards - by the
feed, by the search pass, or by "fix missing episodes" on the entry itself.

**Enrichment.** What Shoko and the files themselves can say, filled in:

* the AniDB id and AniDB's own names, from the dump (build 116);
* the episode rows, from the count AniDB states;
* a cover - the downloaded line-up's when there is one, Shoko's own artwork
  when there is not;
* the subgroup, read out of the file names Shoko holds;
* the quality, read out of the file names, and *out of the files* when the
  name does not say - the same probe the Jellyfin check uses, which is where
  the request came from: *"you can check the file size and actually get it"*.

Nothing here raises. It runs on startup and behind a button, over somebody
else's server, and a watchlist that will not load because Shoko is restarting
is worse than a watchlist with some grey cards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle
from backend.app.services.anime import ensure_episode_rows, normalize_local_name
from backend.app.services.metadata_fill import merged_metadata

logger = for_component("Shoko")

#: How many series one automatic pass adopts. A first run against a library
#: of hundreds should not hold the worker for ten minutes; what is left is
#: taken by the next pass, and the button has no limit at all.
ADOPT_PER_PASS = 50


@dataclass
class AdoptionSummary:
    """What a pass did, in the words the page reports it in."""

    considered: int = 0
    adopted: int = 0
    enriched: int = 0
    skipped: int = 0
    #: Anime whose episode count or air dates were corrected from AniDB.
    recounted: int = 0
    #: Why it could not do more, when that is the interesting part.
    detail: str | None = None
    #: One line per thing that could not be filled in (build 194): the cover
    #: Shoko would not give, the file list it could not read. Said, because
    #: "nothing happened" is not something a person can act on.
    problems: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)


from backend.app.services.anime import ADULT_GENRES  # noqa: E402 - one rule for "adult" (build 192)


def adopt_all(
    session: Session, *, limit: int | None = ADOPT_PER_PASS, enrich_existing: bool = True
) -> AdoptionSummary:
    """Bring every Shoko series onto the watchlist, and fill in the rest."""
    summary = AdoptionSummary()
    from backend.app.services.shoko_watchlist import unlinked

    try:
        series, detail = unlinked(session)
    except Exception as exc:  # noqa: BLE001 - Shoko is somebody else's service
        return AdoptionSummary(detail=str(exc))
    if detail:
        summary.detail = detail
        series = []

    summary.considered = len(series)
    for entry in series[: limit if limit is not None else len(series)]:
        try:
            anime = _adopt_one(session, entry)
        except Exception as exc:  # noqa: BLE001 - one bad series is not a pass
            logger.warning("could not adopt %s from Shoko: %s", entry.name, exc)
            summary.skipped += 1
            continue
        if anime is None:
            summary.skipped += 1
            continue
        summary.adopted += 1
        summary.names.append(anime.display_name)
        # The whole package in one go (build 194): the files, their folder and
        # library, the subgroup and quality, the cover. Not left for a later
        # pass - adopting means the files and the data are already there.
        try:
            enrich(session, anime, shoko_id=getattr(entry, "shoko_id", None), problems=summary.problems)
        except Exception as exc:  # noqa: BLE001 - the entry exists; say what is missing
            summary.problems.append(f"{anime.display_name}: could not be filled in ({exc})")
    if summary.adopted:
        session.commit()

    if enrich_existing:
        summary.enriched = enrich_watchlist(session)
        # And the counts, which is the other half of "all information
        # available" (build 118): an episode AniFlow does not know exists
        # cannot be looked for, however complete the rest of the entry is.
        try:
            summary.recounted = reconcile_episodes(session)
        except Exception as exc:  # noqa: BLE001 - never fail a pass over this
            logger.debug("episode counts could not be reconciled: %s", exc)

    from backend.app.services.integration_state import warm_shoko

    # The card colours are read from a remembered view of Shoko's library, and
    # this pass has just changed what is on both sides of the comparison. Asked
    # again here rather than dropped: this runs on the worker (or behind a
    # button somebody is waiting on), and the alternative is a watchlist that
    # goes grey until something else happens to ask.
    warm_shoko()
    return summary


def _from_anidb(entry) -> "AnimeCandidate | None":
    """Build an entry out of AniDB's own names and Shoko's counts (118).

    Reported: *"I have more stuff in shoko, it did not take everything"*, with
    nineteen series in Shoko and none of them adopted. They were all skipped
    by the rule above - the downloaded season line-up only covers seasons
    somebody has fetched, so Saber Marionette J (1996), Iron Wok Jan and a
    show announced for 2026 were all equally unknown to it.

    But they are not unknown. Shoko states an AniDB id, the weekly title dump
    has every name AniDB files that id under, and Shoko itself knows how many
    episodes there are. That is a real entry: a real id, real titles, a real
    count - and AniDB as the provider, which is honest about where it came
    from and can be refreshed later.

    This is what build 117 refused to do, and it was right to refuse the thing
    it was refusing: inventing a *provider id* from a folder name. An AniDB id
    that Shoko states is the opposite of invented.

    Returns None when the dump has nothing either, which for a brand-new show
    means "try again after the next dump".
    """
    from backend.app.integrations.metadata.base import AnimeCandidate
    from backend.app.services import anidb_titles

    if not entry.anidb_id:
        return None
    names = anidb_titles.names_for(int(entry.anidb_id))
    if not names:
        return None

    main = names.get("main")
    english = names.get("en")
    native = names.get("ja")
    display = english or main or entry.name
    if not display:
        return None
    return AnimeCandidate(
        provider="anidb",
        provider_id=str(int(entry.anidb_id)),
        display_name=display,
        title_romaji=main,
        title_english=english,
        title_native=native,
        # Every other language the dump carries, so a search for the German
        # or Spanish name finds it later.
        aliases=tuple(
            value
            for language, value in names.items()
            if language not in ("main", "en", "ja") and value
        ),
        # Shoko's own count. AniDB is the authority on how many episodes a
        # show has, and this is AniDB's number by way of Shoko.
        episode_count=entry.total_episodes or None,
        provider_ids={"anidb": str(int(entry.anidb_id))},
    )


def adopt_series(session: Session, shoko_id: int) -> tuple[Anime | None, str | None]:
    """Put one Shoko series on the watchlist, by the button on its page (build 243).

    Returns the entry, or None with the reason in words. A series already there is returned as it is.
    The same adoption the worker does for everything, for one series and with the reason when it
    cannot: the worker only counts what it skipped.
    """
    from backend.app.services import anidb_titles
    from backend.app.services.shoko_library import cached_overview

    held, _ = cached_overview()
    if not held.reachable:
        return None, held.detail or "Shoko Server cannot be reached"
    entry = next((row for row in held.series if row.shoko_id == int(shoko_id)), None)
    if entry is None:
        return None, "Shoko does not list this series"
    return adopt_entry(session, entry)


def adopt_entry(session: Session, entry) -> tuple[Anime | None, str | None]:
    """One series (Shoko's, or a row of the AniDB My List) as a watchlist entry, with the reason when not.

    ``entry`` needs ``name``, ``anidb_id``, ``total_episodes`` and ``shoko_id`` (None when Shoko does not
    hold it). A series already on the watchlist is returned as it is.
    """
    from backend.app.services import anidb_titles

    if entry.anidb_id:
        existing = session.scalar(select(Anime).where(Anime.anidb_id == int(entry.anidb_id)))
        if existing is not None:
            return existing, None
    anime = _adopt_one(session, entry)
    if anime is None:
        if not entry.anidb_id:
            return None, "Shoko gives this series no AniDB id, so AniFlow cannot name it"
        if not anidb_titles.names_for(int(entry.anidb_id)):
            return None, "AniFlow's AniDB title list does not know this id yet - it updates weekly, try again after the next update"
        return None, "A watchlist entry with this name or id already exists"
    problems: list[str] = []
    try:
        enrich(session, anime, shoko_id=entry.shoko_id, problems=problems)
    except Exception as exc:  # noqa: BLE001 - the entry exists; say what is missing
        logger.warning("could not fill in %s after adding it: %s", anime.display_name, exc)
    session.commit()
    return anime, None


def _adopt_one(session: Session, entry) -> Anime | None:
    """One Shoko series as a watchlist entry, or None if it cannot be named.

    No network: the downloaded line-up answers by AniDB id, which is the id
    Shoko states. A series nothing has heard of is left for the user to add by
    hand rather than invented from a folder name - an entry with a made-up
    provider id is worse than no entry, because nothing can ever refresh it.
    """
    from backend.app.services import calendar_lookup

    candidate = None
    if entry.anidb_id:
        candidate = calendar_lookup.by_anidb_id(int(entry.anidb_id))
    if candidate is None:
        candidate = _from_anidb(entry)
    if candidate is None:
        return None

    # The same show under another name, or resolved twice in one pass: the
    # database refuses a second row for one provider id, and that refusal
    # would arrive at the flush below and spoil the rest of the pass. Asked
    # first instead (build 180) - the same check adding by hand makes.
    if session.scalar(
        select(Anime).where(
            Anime.primary_provider == candidate.provider,
            Anime.primary_provider_id == candidate.provider_id,
        )
    ):
        return None

    local_name = normalize_local_name(entry.name or candidate.display_name)
    if session.scalar(select(Anime).where(Anime.local_name == local_name)):
        # Somebody already has this name. Not a failure worth reporting: the
        # match it was not recognised by is the interesting part, and that is
        # what the Shoko half of the card is for.
        return None

    anime = Anime(
        primary_provider=candidate.provider,
        primary_provider_id=candidate.provider_id,
        local_name=local_name,
        display_name=candidate.display_name,
        title_english=candidate.title_english,
        title_romaji=candidate.title_romaji,
        title_native=candidate.title_native,
        aliases=list(candidate.aliases),
        cover_url=candidate.cover_url,
        format=candidate.format,
        episode_count=candidate.episode_count or entry.total_episodes or None,
        lifecycle=AnimeLifecycle.ACTIVE,
        provider_ids=dict(candidate.provider_ids or {}),
        # One shape whatever answered (build 123). An adopted entry is
        # built either from the downloaded calendar or from AniDB's title
        # dump, and the second of those knows nothing at all - which is
        # most of why these were the entries with empty Metadata icons.
        metadata_json=merged_metadata({}, candidate),
        air_start_date=candidate.air_start_date,
        air_slot_at=candidate.air_slot_at,
        # Shoko names its own folders, so AniFlow does not invent one. The
        # same rule the add path follows in this mode.
        media_folder=None,
        # On, because adopting is the decision to have AniFlow look after it.
        automation_enabled=True,
        # What the source says; the user can change it on the anime's page.
        is_adult=bool(candidate.is_adult)
        or any(str(g).strip().casefold() in ADULT_GENRES for g in candidate.genres or ()),
    )
    session.add(anime)
    session.flush()

    from backend.app.services.anidb_identity import adopt as adopt_names

    try:
        adopt_names(anime, int(entry.anidb_id) if entry.anidb_id else None)
    except Exception as exc:  # noqa: BLE001 - names are not worth a failure
        logger.debug("no AniDB names for %s: %s", local_name, exc)

    ensure_episode_rows(session, anime, anime.episode_count)
    session.flush()
    logger.info("adopted %s from Shoko Server", anime.display_name)
    return anime


def reconcile_episodes(session: Session) -> int:
    """Take AniDB's episode count and air dates, where AniFlow has less (118).

    From a correction to a bug report. Iron Wok Jan read *11 / 11* on the
    watchlist and was missing an episode: *"I did check anidb and it has
    actually 12 episodes"*. AniFlow's count comes from AniList or Kitsu, and
    for older shows theirs is simply lower - so an incomplete season looked
    complete, and nothing anywhere disagreed.

    AniDB is the better authority for how many episodes a show has, and Shoko
    states it for everything it holds. Two things are taken:

    * the count, when AniDB's is higher - and the episode rows to match, so
      the missing ones can be looked for at all;
    * the air date of each episode Shoko is missing, which is what lets
      everything else tell "not here yet" from "not aired yet".

    Never lowers a count. A provider saying twelve and AniDB saying eleven is
    a disagreement this cannot settle, and dropping rows might drop one with
    a file behind it.

    Returns how many anime were changed.
    """
    from datetime import datetime

    from backend.app.models.anime import Episode
    from backend.app.services.shoko_library import holdings

    facts, answered = holdings()
    if not answered or not facts:
        return 0

    changed = 0
    for anime in session.scalars(select(Anime)):
        fact = _fact_for(anime, facts)
        if fact is None:
            continue
        touched = False

        known = int(anime.episode_count or 0)
        total = int(fact.total_episodes or 0)
        if total > known:
            logger.info(
                "%s: AniDB has %s episodes, AniFlow had %s",
                anime.display_name, total, known,
            )
            anime.episode_count = total
            ensure_episode_rows(session, anime, total)
            session.flush()
            touched = True

        # The air dates, so "missing" and "not out yet" stop looking alike.
        if fact.missing:
            rows = {
                episode.number: episode
                for episode in session.scalars(
                    select(Episode).where(Episode.anime_id == anime.id)
                )
            }
            for number, when in fact.missing:
                episode = rows.get(number)
                if episode is None or episode.aired_at is not None or not when:
                    continue
                try:
                    episode.aired_at = datetime.fromisoformat(str(when)[:10])
                except ValueError:
                    continue
                touched = True

        if touched:
            changed += 1
    if changed:
        session.commit()
    return changed


def _fact_for(anime: Anime, facts: dict):
    """What Shoko says about this anime, by id and then by name."""
    from backend.app.services.anidb_titles import normalize

    aid = anime.anidb_id or None
    if aid is None:
        raw = (anime.provider_ids or {}).get("anidb")
        aid = int(raw) if str(raw or "").isdigit() else None
    if aid is not None and aid in facts:
        return facts[aid]

    wanted = {
        normalize(title)
        for title in (
            anime.display_name,
            anime.local_name,
            anime.title_romaji,
            anime.title_english,
            (anime.anidb_titles or {}).get("main"),
        )
        if title
    }
    for fact in facts.values():
        if normalize(fact.name) in wanted:
            return fact
    return None


def enrich_watchlist(session: Session, *, only_id: str | None = None) -> int:
    """Fill in what is missing on entries that already exist.

    Returns how many were changed. Deliberately additive: nothing already set
    is overwritten, because a value the user chose outranks one read off a
    file name.
    """
    statement = select(Anime)
    if only_id:
        statement = statement.where(Anime.id == only_id)
    changed = 0
    for anime in session.scalars(statement):
        try:
            if enrich(session, anime):
                changed += 1
        except Exception as exc:  # noqa: BLE001 - one entry is not the pass
            logger.debug("could not enrich %s: %s", anime.local_name, exc)
    if changed:
        session.commit()
    return changed


def _library_from_files(session: Session, anime: Anime, files) -> bool:
    """File the anime in the library linked to the Shoko folder its files are in.

    Only for an anime that has no library yet: one the user placed somewhere is
    not second-guessed by where a file happens to be. Several folders - a show
    split over two - go with the one holding most files.
    """
    from collections import Counter

    from backend.app.models.library import Library

    if anime.library_id is not None:
        return False
    counts = Counter(entry.import_folder_id for entry in files if entry.import_folder_id is not None)
    for folder_id, _ in counts.most_common():
        library = session.scalar(
            select(Library).where(Library.shoko_folder_id == folder_id).order_by(Library.sort_order)
        )
        if library is not None:
            anime.library_id = library.id
            anime.library = library
            return True
    return False


def _never_linked(session: Session, anime: Anime) -> bool:
    """Whether this anime has no files recorded at all.

    The bootstrap question, and deliberately not "is anything missing": an
    airing show is missing next week's episode every week, and asking Shoko
    about it on every pass would be a request per anime for ever. Once one
    file is recorded and the folder is known, AniFlow's own library pass
    walks that folder and this has nothing left to do.
    """
    from backend.app.models.anime import Episode

    if anime.media_folder:
        return False
    return not session.scalar(
        select(Episode.id)
        .where(Episode.anime_id == anime.id, Episode.media_filename.is_not(None))
        .limit(1)
    )


def link_shoko_files(
    session: Session,
    anime: Anime,
    *,
    shoko_id: int | None = None,
    problems: list[str] | None = None,
) -> list[str]:
    """Record the files Shoko holds against this anime's episodes (119).

    Reported after adoption started working: two adopted series listed every
    episode as *"Not available"* with the files sitting in the library the
    whole time. Nothing had ever looked: the Shoko resolver only considers
    episodes AniFlow itself handed over, and an adopted series has handed
    over nothing.

    Three things come out of one request, and each was separately missing:

    * the file name per episode, so the list says Owned instead of nothing;
    * the folder Shoko filed them under, so every later pass can find them -
      an adopted anime had no library folder at all, which is why the
      quality probe had nothing to open either;
    * the picture height, which answers *"can this info not be obtainable?"*
      for a library of DVD rips whose names carry no resolution.

    Returns what it filled in. Never raises.
    """
    from backend.app.services.episode_state import record_media_file, refresh_episode_status
    from backend.app.models.anime import Episode

    aid = anime.anidb_id
    if not aid and not shoko_id:
        return []
    files, reason = [], None
    try:
        from backend.app.services import shoko_library

        # Shoko's own series id first when it is known; the AniDB id is the
        # second way in, and both are tried before giving up.
        if shoko_id:
            files, reason = shoko_library.series_episode_files(int(shoko_id))
        if not files and aid:
            files = shoko_library.episode_files_for(int(aid))
    except Exception as exc:  # noqa: BLE001 - Shoko may be restarting
        logger.debug("no episode files for %s: %s", anime.local_name, exc)
        reason = str(exc)
    if not files:
        if problems is not None:
            problems.append(f"{anime.display_name}: no files linked - {reason or 'Shoko lists none'}")
        return []

    filled: list[str] = []
    rows = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
    }

    # The folder Shoko chose, taken rather than invented. In this mode Shoko
    # owns the naming, so where the files are *is* the answer - the same rule
    # services/shoko.py follows when it recognises a handed-over file.
    folders = [entry.folder for entry in files if entry.folder]
    if folders and not anime.media_folder:
        anime.media_folder = max(set(folders), key=folders.count)[:255]
        filled.append("media_folder")

    # And the library, from which of Shoko's folders holds them (build 194):
    # the library linked to that folder is where this anime lives.
    if _library_from_files(session, anime, files):
        filled.append("library")

    linked = 0
    for entry in files:
        episode = rows.get(entry.number)
        if episode is None or episode.media_filename:
            continue
        # Version 1 rather than nothing (build 135): a null media_version
        # makes the duplicate check read "nothing on disk to compare with" and
        # offer the episode for download again. Nothing here knows the real
        # version - Shoko had the file before AniFlow did - and a file with no
        # version marker is v1 by the same convention as everywhere else.
        record_media_file(
            episode, filename=entry.filename, size_bytes=entry.size, version=1
        )
        refresh_episode_status(episode)
        linked += 1
    if linked:
        filled.append(f"{linked} files")

    # And the quality, from what Shoko measured when it read the file.
    heights = [entry.height for entry in files if entry.height]
    if heights and _fill_resolution(session, anime, _label_for(max(heights))):
        filled.append("quality")
    return filled


def _label_for(height: int) -> str:
    """The word people use for a picture that tall."""
    for edge, label in ((2000, "2160p"), (1000, "1080p"), (680, "720p"), (0, "480p")):
        if height > edge:
            return label
    return "480p"


def _fill_resolution(session: Session, anime: Anime, label: str) -> bool:
    """Write a quality onto the profile, if it has none."""
    from backend.app.models.release_profile import ReleaseProfile

    profile = session.scalars(
        select(ReleaseProfile).where(ReleaseProfile.anime_id == anime.id)
    ).first()
    if profile is None:
        profile = ReleaseProfile(anime_id=anime.id)
        session.add(profile)
    if profile.resolution:
        return False
    profile.resolution = label
    return True


def enrich(
    session: Session,
    anime: Anime,
    *,
    shoko_id: int | None = None,
    problems: list[str] | None = None,
) -> list[str]:
    """Fill in one entry from Shoko and from its own files.

    Returns what was filled in, as field names, so a caller can say what it
    did rather than only how many.
    """
    filled: list[str] = []

    # --- AniDB's id and names, for anything that predates build 116 --------
    if anime.anidb_id is None or not anime.anidb_titles:
        from backend.app.services.anidb_identity import adopt as adopt_names

        if adopt_names(anime) is not None:
            filled.append("anidb")

    # --- a picture ---------------------------------------------------------
    if not (anime.cover_url or anime.custom_cover):
        if _fill_cover(anime, shoko_id=shoko_id, problems=problems):
            filled.append("cover")

    # --- the files Shoko already holds -------------------------------------
    #
    # First, because everything below reads better with them: the file names
    # answer the subgroup, Shoko's own media info answers the quality, and
    # the folder they are in is what lets any later pass find them.
    if _never_linked(session, anime):
        filled += link_shoko_files(session, anime, shoko_id=shoko_id, problems=problems)

    # --- what the files say ------------------------------------------------
    #
    # Only when something is actually missing. This runs over the whole
    # watchlist every half hour, and asking Shoko about an anime whose profile
    # is already complete would be two requests per anime per pass for an
    # answer that changes nothing - two hundred series, nine thousand requests
    # a day, all of them ignored.
    if _needs_profile(session, anime):
        names = _file_names(anime)
        if names and _fill_profile(session, anime, names):
            filled.append("profile")

    # --- the library ---------------------------------------------------------
    #
    # Nothing is on the watchlist without one (build 192). The files' own
    # location has already answered it when it could; this is the fallback.
    if anime.library_id is None:
        from backend.app.services import libraries

        default = libraries.default_library(session)
        anime.library_id = default.id
        anime.library = default
        filled.append("library")

    return filled


def _needs_profile(session: Session, anime: Anime) -> bool:
    """Whether anything about this entry's release is still unknown."""
    from backend.app.models.release_profile import ReleaseProfile

    if not anime.release_name:
        return True
    profile = session.scalars(
        select(ReleaseProfile).where(ReleaseProfile.anime_id == anime.id)
    ).first()
    return profile is None or not profile.subgroups or not profile.resolution


def _fill_cover(
    anime: Anime, *, shoko_id: int | None = None, problems: list[str] | None = None
) -> bool:
    """A poster from the downloaded line-up, or from Shoko itself."""
    aid = anime.anidb_id
    if aid:
        try:
            from backend.app.services.calendar_lookup import by_anidb_id

            found = by_anidb_id(int(aid))
        except Exception:  # noqa: BLE001
            found = None
        if found is not None and found.cover_url:
            anime.cover_url = found.cover_url
            return True

    # Shoko's own artwork, which is the half of the request the line-up
    # cannot answer: *"it should also add a cover picture from Shoko server
    # if there is something"*.
    #
    # Fetched and stored as this anime's own cover rather than linked: Shoko
    # wants its API key for images, the browser has no business holding one,
    # and a stored picture keeps working while Shoko restarts.
    if not aid and not shoko_id:
        return False
    try:
        from backend.app.services.covers import store_cover
        from backend.app.services import shoko_library

        # By Shoko's own series id when the caller has it - one request fewer,
        # and no lookup by AniDB id that can quietly come back empty - then by
        # AniDB id.
        data = shoko_library.poster_bytes_for_series(int(shoko_id)) if shoko_id else None
        if not data and aid:
            data = shoko_library.poster_bytes_for(int(aid))
        if not data:
            if problems is not None:
                problems.append(f"{anime.display_name}: no cover - not in the calendar, and Shoko has no poster")
            return False
        anime.custom_cover = store_cover(anime.id, data)
        return True
    except Exception as exc:  # noqa: BLE001 - a picture is never worth a 500
        logger.debug("no Shoko poster for %s: %s", anime.local_name, exc)
        if problems is not None:
            problems.append(f"{anime.display_name}: no cover ({exc})")
        return False


def _file_names(anime: Anime) -> list[str]:
    """Every file name AniFlow knows for this anime.

    Its own records first - the episodes carry the name of whatever was
    imported - and Shoko's file list for a series AniFlow has never
    downloaded, which is every series this build adopts.
    """
    names = [
        episode.media_filename or episode.import_filename or episode.download_filename
        for episode in anime.episodes
    ]
    names = [name for name in names if name]
    if names:
        return names

    aid = anime.anidb_id
    if not aid:
        return []
    try:
        from backend.app.services.shoko_library import file_names_for

        return file_names_for(int(aid))
    except Exception as exc:  # noqa: BLE001 - Shoko may be restarting
        logger.debug("no Shoko file list for %s: %s", anime.local_name, exc)
        return []


def learn_from_files(session: Session, anime: Anime) -> bool:
    """Fill the release profile from the files the anime has, as one arrives (build 222).

    Reported: an anime added without choosing a release got its first episode
    in the library, and its profile still read "Custom (manual)" - the group
    and quality were written on the file and nowhere else. They were only ever
    learned by the manual "Fill in missing" pass and by Shoko adoption, never
    when a file landed. So the library fills in what it learns from what it
    receives.

    Additive, exactly as the pass is: only an empty subgroup or quality is
    written, a value anybody set is never touched. Names only - the file is not
    opened here (the pass does that, for a name that carries no quality).
    All three names an episode has are read, because the download's own name
    states the quality and the renamed library file often does not.
    """
    if not _needs_profile(session, anime):
        return False
    names: list[str] = []
    for episode in anime.episodes:
        for name in (episode.download_filename, episode.import_filename, episode.media_filename):
            if name and name not in names:
                names.append(name)
    return bool(names) and _fill_profile(session, anime, names, probe=False)


def _fill_profile(session: Session, anime: Anime, names: list[str], *, probe: bool = True) -> bool:
    """The subgroup and the quality, from the names and then from the files.

    The subgroup is in the name or it is nowhere - that is how release groups
    publish. The quality is usually in the name too, and when it is not (the
    reported case) the file itself is asked, which is the same probe the
    Jellyfin readiness check uses. ``probe=False`` reads the names only.
    """
    from backend.app.models.release_profile import ReleaseProfile
    from backend.app.services.release_parser import parse_release_name

    from backend.app.services.release_edition import markers_in

    subgroups: list[str] = []
    resolutions: list[str] = []
    #: What the names say about the source (WebRip, BD ...) and the edition (uncensored, dual-audio ...), per file.
    #: Learned only where every file says the same (item 46): a library holding two sources is not one source.
    sources: list[str | None] = []
    editions: list[tuple[str, ...]] = []
    for name in names:
        parsed = parse_release_name(Path(name).name)
        sources.append(getattr(parsed, "media_source", None))
        editions.append(tuple(marker for marker in markers_in(Path(name).name) if marker != "repack"))
        group = getattr(parsed, "subgroup", None)
        if group and group not in subgroups:
            subgroups.append(group)
        quality = getattr(parsed, "resolution", None)
        if quality and quality not in resolutions:
            resolutions.append(quality)

    if not resolutions and probe:
        measured = _resolution_from_disk(anime)
        if measured:
            resolutions.append(measured)

    one_source = sources[0] if sources and sources[0] and len(set(sources)) == 1 else None
    one_edition = list(editions[0]) if editions and editions[0] and len(set(editions)) == 1 else []

    if not subgroups and not resolutions and not one_source and not one_edition:
        return False

    profile = session.scalars(
        select(ReleaseProfile).where(ReleaseProfile.anime_id == anime.id)
    ).first()
    if profile is None:
        profile = ReleaseProfile(anime_id=anime.id)
        session.add(profile)
    changed = False
    if subgroups and not profile.subgroups:
        # One group, not all of them: a profile listing every group a library
        # happens to contain would accept every group in future, which is the
        # opposite of what a profile is for.
        profile.subgroups = subgroups[:1]
        changed = True
    if resolutions and not profile.resolution:
        profile.resolution = resolutions[0]
        changed = True
    if one_source and not profile.media_source:
        profile.media_source = one_source
        changed = True
    if one_edition and not profile.edition:
        profile.edition = one_edition
        changed = True
    if changed and not anime.release_name:
        # The name releases are matched by, from the same file names.
        stem = Path(names[0]).stem
        parsed = parse_release_name(Path(names[0]).name)
        anime.release_name = (getattr(parsed, "title", None) or stem)[:255]
    return changed


def _resolution_from_disk(anime: Anime) -> str | None:
    """Ask a real file how tall it is (build 117).

    *"Can you use the same setup which you use to check if the anime is ready
    for jellyfin, to also get the resolution?"* - yes: services/media_probe.py,
    which reads the container directly and needs no ffmpeg for Matroska.
    """
    from backend.app.services import media_probe
    from backend.app.services.storage import media_path, roots_for, storage_roots

    roots = roots_for(anime, storage_roots())
    for episode in anime.episodes:
        if not episode.media_filename:
            continue
        target = media_path(roots, anime.media_folder or anime.local_name, episode.media_filename)
        if target is None or not target.exists():
            continue
        try:
            info = media_probe.probe(target)
        except Exception:  # noqa: BLE001 - an unreadable file is not an error
            continue
        video = info.video
        height = getattr(video, "height", None) if video else None
        if not height:
            continue
        # The label people use, not the exact number: 1080p covers 1080 and
        # the odd 1082 a bad encode reports.
        for edge, label in ((2000, "2160p"), (1000, "1080p"), (680, "720p"), (0, "480p")):
            if height > edge:
                return label
    return None


__all__ = [
    "ADOPT_PER_PASS",
    "AdoptionSummary",
    "adopt_all",
    "enrich",
    "enrich_watchlist",
    "learn_from_files",
]

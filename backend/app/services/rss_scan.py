"""RSS scan orchestration.

For every enabled feed: fetch items, skip already-known GUIDs (duplicate
prevention), parse each release name deterministically and evaluate it against
the tracked anime's release profiles. Every new item is recorded together with
its verdict so exceptional states stay auditable (AGENTS.md business rules).

This module is synchronous on purpose: the worker executes it through a
worker thread so HTTP handlers never block on network or database work.
"""
from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.integrations.rss.base import FeedFetcher, RssItem, fetch_feed
from backend.app.models.anime import Anime, AnimeLifecycle, Episode
from backend.app.models.release_profile import ReleaseProfile
from backend.app.models.rss import FeedSource, Release, ReleaseDecision
from backend.app.models.activity import AttentionKind
from backend.app.services.attention import log_activity, raise_attention, resolve_open
from backend.app.services.episode_releases import attach_release_to_episode
from backend.app.services.matching import evaluate_release, unknown_is_fine
from backend.app.services.release_identity import anime_reference_name, matching_anime
from backend.app.services.release_parser import ParsedRelease, parse_release_name

logger = for_component("RSS")

_TRACKED_LIFECYCLES = (AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING)


@dataclass
class ScanSummary:
    feeds_total: int = 0
    feeds_ok: int = 0
    feeds_failed: int = 0
    items_seen: int = 0
    #: Rejections not written down because the episode is already in the
    #: library. Counted rather than silent, so the log can say the list is
    #: short on purpose.
    rejections_skipped: int = 0
    duplicates_skipped: int = 0
    episodes_linked: int = 0
    decisions: dict[str, int] = field(default_factory=dict)

    def count(self, decision: ReleaseDecision) -> None:
        self.decisions[decision.value] = self.decisions.get(decision.value, 0) + 1


def _candidate_titles(anime: Anime) -> list[str]:
    """Names the profile's title check may compare against.

    Identity is already settled by ``release_identity`` before this is used;
    these only keep ``require_title_match`` from rejecting a release that was
    just positively identified. The stored release name comes first because it
    is the one providers actually use.
    """
    titles = [
        anime_reference_name(anime),
        anime.display_name,
        anime.local_name,
        anime.title_english,
        anime.title_romaji,
        anime.title_native,
        *(anime.aliases or []),
    ]
    return [title for title in titles if title]


def _tracked_anime(session: Session) -> list[Anime]:
    statement = select(Anime).where(
        Anime.lifecycle.in_(_TRACKED_LIFECYCLES),
        # A tracker entry (added in Basic mode) has no release name, so there
        # is nothing for a feed item to match against. Left out here rather
        # than filtered later, so it never reaches a pass that would count it
        # as missing something.
        Anime.entry_mode != "basic",
        # And one the user has asked to be left alone (build 123). Filtered
        # here rather than after matching, for the same reason as the line
        # above: a feed item that matches it would be recorded as a find,
        # and a find is the start of everything this is meant to stop.
        Anime.releases_ignored.is_(False),
    )
    return list(session.scalars(statement))


def _evaluate_item(
    item: RssItem, tracked: list[Anime]
) -> tuple[ReleaseDecision, Anime | None, list[str], list[str], ParsedRelease | None]:
    """Decide the verdict for one feed item without touching the database."""
    try:
        parsed = parse_release_name(item.title)
    except ValueError:
        return ReleaseDecision.IGNORED, None, ["unparseable_name"], [], None

    # Match on the name releases use for the show, season-aware. Comparing
    # against metadata titles never worked: the parser splits the season off,
    # so "Youjo Senki" was tested against "Youjo Senki S2" and always lost.
    title_candidates = matching_anime(parsed, tracked)

    if not title_candidates:
        return ReleaseDecision.IGNORED, None, ["untracked_title"], [], parsed
    with_profile = [anime for anime in title_candidates if anime.profile is not None]
    without_profile = [anime for anime in title_candidates if anime.profile is None]
    if not with_profile:
        return ReleaseDecision.IGNORED, None, ["no_profile"], [anime.id for anime in without_profile], parsed
    if len(with_profile) > 1:
        return ReleaseDecision.AMBIGUOUS, None, [], [anime.id for anime in with_profile], parsed

    anime = with_profile[0]
    profile: ReleaseProfile = anime.profile
    result = evaluate_release(
        parsed,
        subgroups=list(profile.subgroups),
        resolution=profile.resolution,
        media_source=profile.media_source,
        edition=profile.edition or [],
        edition_not=profile.edition_not or [],
        censor_preference=profile.censor_preference.value,
        candidate_titles=_candidate_titles(anime),
        require_title_match=profile.require_title_match,
        require_resolution_known=profile.require_resolution_known,
        require_source_known=profile.require_source_known,
        require_censor_known=profile.require_censor_known,
        tolerate_unknown=unknown_is_fine(anime, profile),
    )
    decision = ReleaseDecision.MATCHED if result.matched else ReleaseDecision.REJECTED
    return decision, anime, result.failures, [], parsed


def _link_to_episode(
    session: Session, anime: Anime, item: RssItem, parsed: ParsedRelease
) -> Episode | None:
    """Make a matched feed item visible on its episode.

    This is the step that was missing entirely: the scan used to record a
    verdict and stop, so a MATCHED release produced nothing the UI could show
    and nothing the queue could pick up.

    Returns the episode when a *new* release was linked, so the caller can
    report it in Activity (by episode, not just by name); None when there was
    nothing new.
    """
    result = attach_release_to_episode(
        session,
        anime,
        parsed,
        provider="rss",
        provider_id=item.guid[:80],
        raw_name=item.title,
        # A magnet built from the info hash keeps working after the item drops
        # off the feed; the .torrent link does not.
        magnet_url=item.magnet_url,
        torrent_url=item.link,
        size=item.size,
        seeders=item.seeders,
        info_hash=item.info_hash,
    )
    if result.skipped_reason is not None:
        logger.debug("feed item not linked (%s): %s", result.skipped_reason, item.title)
        return None
    if not result.created:
        return None
    return result.episode


def _settled_already(session: Session, anime: Anime, parsed: ParsedRelease) -> bool:
    """Whether this release is about an episode that is finished business.

    A season feed carries every subgroup's copy of every episode, and each one
    that does not fit the profile was written down as a rejection. For an
    episode already sitting in the library at this version or better, that
    line can never be acted on: it says a group you did not choose released a
    quality you did not ask for, of something you already have.

    A higher version is a different matter and is always recorded - a v2 of an
    owned episode is exactly the case the user wants to hear about.
    """
    if parsed.episode is None:
        return False
    episode = session.scalar(
        select(Episode).where(Episode.anime_id == anime.id, Episode.number == parsed.episode)
    )
    if episode is None or not episode.media_filename:
        return False
    return (parsed.version or 1) <= (episode.media_version or 1)


def _batch_adds_nothing_new(session: Session, anime: Anime, parsed: ParsedRelease) -> bool:
    """Whether every episode a batch covers is already owned at this version.

    Reported live (169): a full-season repack for a show already complete in
    the library read as MATCHED - title, subgroup and quality all fit, which
    is everything ``evaluate_release`` checks, and it has no notion of
    episode ownership at all. Nothing about a batch like that is actually
    queueable, so it is not a match: checked here, per number, through
    ``episode_number_for`` - the same offset-aware conversion queueing itself
    uses, so an anime whose numbering does not start at 1 is judged the same
    way there.

    A batch missing even one episode, or carrying a higher version of one
    already owned, is still a real match - that is the case worth hearing
    about, the same reasoning ``_settled_already`` uses for a single episode.
    """
    from backend.app.services.episode_state import episode_number_for

    if parsed.episode_start is None or parsed.episode_end is None:
        return False
    if parsed.episode_end < parsed.episode_start:
        return False
    numbers = {
        number
        for raw in range(parsed.episode_start, parsed.episode_end + 1)
        if (number := episode_number_for(anime, str(raw))) is not None
    }
    if not numbers:
        return False
    owned = dict(
        session.execute(
            select(Episode.number, Episode.media_version).where(
                Episode.anime_id == anime.id,
                Episode.number.in_(numbers),
                Episode.media_filename.is_not(None),
            )
        ).all()
    )
    version = parsed.version or 1
    return all((owned.get(number) or 0) >= version for number in numbers)


def _drop_settled_rejections(session: Session, source: FeedSource) -> int:
    """Remove rejections for episodes this feed has already matched.

    A season feed carries every group's copy of every episode. When one of
    them matched, the rest are a record of a decision that has been made -
    true, unactionable, and three lines of scrolling per episode.

    Done as a sweep rather than a check at the time because the order items
    arrive in is the feed's business: a rejection written before the match was
    correct when it was written. A *higher* version is never swept - a v2 of
    an episode that already matched is exactly the case worth hearing about.
    """
    matched = session.execute(
        select(Release.anime_id, Release.episode, func.max(Release.version))
        .where(
            Release.feed_source_id == source.id,
            Release.decision == ReleaseDecision.MATCHED,
            Release.anime_id.is_not(None),
            Release.episode.is_not(None),
        )
        .group_by(Release.anime_id, Release.episode)
    ).all()
    if not matched:
        return 0

    removed = 0
    for anime_id, episode, version in matched:
        rows = session.scalars(
            select(Release).where(
                Release.feed_source_id == source.id,
                Release.decision == ReleaseDecision.REJECTED,
                Release.anime_id == anime_id,
                Release.episode == episode,
                Release.version <= (version or 1),
            )
        ).all()
        for row in rows:
            session.delete(row)
            removed += 1
    if removed:
        logger.debug("%s rejection(s) dropped: the episode was matched", removed)
    return removed


def _record_item(
    session: Session, source: FeedSource, item: RssItem, summary: ScanSummary
) -> ReleaseDecision:
    tracked = _tracked_anime(session)
    decision, anime, failures, ambiguous, parsed = _evaluate_item(item, tracked)

    # A batch that matches the profile but adds nothing new is not a match -
    # see _batch_adds_nothing_new. Demoted before anything below reads
    # `decision`, so it is recorded, filtered and counted as the rejection it
    # actually is rather than as a match nothing came of.
    if (
        decision is ReleaseDecision.MATCHED
        and anime is not None
        and parsed is not None
        and _batch_adds_nothing_new(session, anime, parsed)
    ):
        decision = ReleaseDecision.REJECTED
        failures = ["already_owned"]

    # Rejections about episodes that are done are not recorded at all. Not
    # hidden behind a filter: a row nobody can act on is not worth the storage
    # or the scrolling, and the GUID is remembered either way so the item is
    # never re-examined.
    if (
        decision is ReleaseDecision.REJECTED
        and anime is not None
        and parsed is not None
        and _settled_already(session, anime, parsed)
    ):
        logger.debug(
            "not recording %r: episode %s of %s is already in the library",
            item.title,
            parsed.episode,
            anime.display_name,
        )
        summary.rejections_skipped += 1
        return decision

    release = Release(
        feed_source_id=source.id,
        guid=item.guid[:255],
        raw_name=item.title[:500],
        torrent_url=item.link,
        published_at=item.published_at,
        parsed_title=(parsed.title[:500] if parsed and parsed.title else None),
        subgroup=parsed.subgroup if parsed else None,
        episode=parsed.episode if parsed else None,
        version=parsed.version if parsed else 1,
        resolution=parsed.resolution if parsed else None,
        media_source=parsed.media_source if parsed else None,
        video_codec=parsed.video_codec if parsed else None,
        audio_codec=parsed.audio_codec if parsed else None,
        censored=parsed.censored if parsed else None,
        unknown_tags=list(parsed.unknown_tags) if parsed else [],
        decision=decision,
        anime_id=anime.id if anime is not None else None,
        failure_reasons=failures,
        ambiguous_anime_ids=ambiguous,
    )
    session.add(release)

    if decision is ReleaseDecision.MATCHED and anime is not None and parsed is not None:
        linked = _link_to_episode(session, anime, item, parsed)
        if linked is not None:
            summary.episodes_linked += 1
            described = f"Episode {linked.number} of {anime.display_name}"
            logger.info("linked %s from feed %s", described, source.name)
            log_activity(
                session,
                kind="RSS_EPISODE_FOUND",
                message=f"RSS found {described} - added to the watchlist",
                details={
                    "feed": source.name,
                    "release": item.title[:200],
                    "episode": parsed.episode,
                },
                anime_id=anime.id,
                episode_id=linked.id,
            )
    return decision


def _hunting(session: Session, what: str) -> bool:
    """Whether AniFlow is looking for releases at all (build 140).

    False in Basic mode, and then nothing downstream runs. Basic is what
    AniFlow is when it has no indexer - or when the user has said they only
    want a watchlist - and the install wizard puts it plainly: *"A watchlist
    and a calendar ... Nothing is downloaded."*

    That promise was being kept by hiding the pages while the passes carried
    on: a feed left over from an indexer file filled every episode of every
    tracked show with magnet links and Download buttons. So the check is here,
    at the entry point, rather than at each of the places downstream that
    would otherwise have to remember it.
    """
    from backend.app.services.app_mode import AppMode as Scope
    from backend.app.services.app_mode import current_mode as scope_of

    if scope_of(session) is Scope.ADVANCED:
        return True
    logger.info("basic mode: no %s, nothing here hunts for releases", what)
    return False


def run_rss_scan(
    session_factory: sessionmaker[Session],
    *,
    fetcher: FeedFetcher | None = None,
) -> ScanSummary:
    """Scan every enabled feed once. Safe to call repeatedly: known GUIDs are skipped."""
    settings = get_settings()
    effective_fetcher: FeedFetcher = fetcher or (lambda url: fetch_feed(url, timeout=settings.external_request_timeout_seconds))

    summary = ScanSummary()
    session = session_factory()
    try:
        if not _hunting(session, "feed scan"):
            return summary
        sources = list(session.scalars(select(FeedSource).where(FeedSource.enabled.is_(True))))
        summary.feeds_total = len(sources)
        from backend.app.integrations.rss.base import FeedError

        for source in sources:
            try:
                items = effective_fetcher(source.url)
            except FeedError as exc:
                logger.warning("feed %s (%s) failed: %s", source.name, source.url, exc)
                summary.feeds_failed += 1
                raise_attention(session, kind=AttentionKind.FEED_FAILED, feed_source_id=source.id, detail=str(exc)[:120])
                continue
            except Exception as exc:  # noqa: BLE001 - a broken feed must never abort the others
                logger.warning("feed %s (%s) failed", source.name, source.url, exc_info=True)
                summary.feeds_failed += 1
                raise_attention(session, kind=AttentionKind.FEED_FAILED, feed_source_id=source.id, detail=str(exc)[:120])
                continue
            # The feed works again - any open failure record clears itself.
            resolve_open(session, kind=AttentionKind.FEED_FAILED, feed_source_id=source.id)
            summary.feeds_ok += 1
            # Batch-load known GUIDs once per feed instead of one query per item.
            known_guids = set(
                session.scalars(select(Release.guid).where(Release.feed_source_id == source.id))
            )
            for item in items:
                summary.items_seen += 1
                if item.guid[:255] in known_guids:
                    summary.duplicates_skipped += 1
                    continue
                decision = _record_item(session, source, item, summary)
                known_guids.add(item.guid[:255])
                summary.count(decision)
            # Once every item of this feed has been judged, the rejections
            # about episodes that were matched in the same pass are noise.
            summary.rejections_skipped += _drop_settled_rejections(session, source)
        # No Activity entry for a pass that ran (build 125). At a
        # sixty-minute interval this alone wrote about 8,760 rows a year, none
        # of which named anything - and the same summary is already on the RSS
        # status box, from worker_state.last_scan_summary. What the scan found
        # is reported per episode by RSS_EPISODE_FOUND instead.
        logger.info(
            "RSS scan finished: %s feed(s) ok, %s failed, %s item(s) seen, %s episode(s) linked",
            summary.feeds_ok,
            summary.feeds_failed,
            summary.items_seen,
            summary.episodes_linked,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return summary


def summarize(scan_summary: ScanSummary) -> dict[str, object]:
    return asdict(scan_summary)


__all__ = ["ScanSummary", "run_rss_scan", "summarize"]

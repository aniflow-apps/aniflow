"""RSS Analyze: run the matching engine across feeds (read-only)."""
import time
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.integrations.rss.base import FeedFetcher, RssItem, fetch_feed
from backend.app.matching_engine import AnimeCandidateInfo, analyze_release, build_prefilter
from backend.app.models.activity import AttentionKind
from backend.app.models.anime import Anime, AnimeLifecycle
from backend.app.models.analysis import AliasSource, AnalysisRun, AnimeAlias
from backend.app.models.release_profile import ReleaseProfile
from backend.app.models.rss import FeedSource
from backend.app.services.attention import raise_attention

_MAX_ITEMS = 500


def _load_candidates(session: Session) -> list[AnimeCandidateInfo]:

    anime_list = session.scalars(
        select(Anime).where(
            Anime.lifecycle.in_([AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING]),
            # A tracker entry cannot be matched against a release name it does
            # not have.
            Anime.entry_mode != "basic",
        )
    ).all()
    infos: list[AnimeCandidateInfo] = []
    for anime in anime_list:
        table_aliases = [
            a.value for a in session.scalars(select(AnimeAlias).where(AnimeAlias.anime_id == anime.id))
        ]
        aliases = [*(anime.aliases or []), *table_aliases]
        profile = session.scalar(select(ReleaseProfile).where(ReleaseProfile.anime_id == anime.id))
        episodes = set(
            session.scalars(
                select(EpisodeRef.number).where(EpisodeRef.anime_id == anime.id)
            )
        )
        infos.append(
            AnimeCandidateInfo(
                anime_id=anime.id,
                display_name=anime.display_name,
                aliases=aliases,
                title_english=anime.title_english,
                title_romaji=anime.title_romaji,
                title_native=anime.title_native,
                episode_numbers=episodes or None,
                pinned_resolution=profile.resolution if profile else None,
                pinned_subgroups=list(profile.subgroups) if profile else [],
            )
        )
    return infos


# Local import shim to avoid a circular module import at load time.
from backend.app.models.anime import Episode as EpisodeRef  # noqa: E402


def run_analysis(
    session_factory: sessionmaker[Session],
    source_ids: list[str],
    *,
    fetcher: FeedFetcher | None = None,
    extra_url: str | None = None,
) -> dict[str, object]:
    """Analyze feed items against active anime.

    An empty ``source_ids`` means every enabled feed, which is what a
    scheduled scan reads - the Configuration window asks "is this show
    findable?", and that question is not about one feed.

    Read-only for the download pipeline: nothing is queued and no media is
    touched. The run header is persisted; individual results are ephemeral.
    """
    settings = get_settings()
    effective_fetcher: FeedFetcher = fetcher or (
        lambda url: fetch_feed(url, timeout=settings.external_request_timeout_seconds)
    )

    started = time.perf_counter()
    session = session_factory()
    try:
        sources = list(
            session.scalars(
                select(FeedSource).where(FeedSource.id.in_(source_ids))
                if source_ids
                else select(FeedSource).where(FeedSource.enabled.is_(True))
            )
        )
        candidates = _load_candidates(session)
        prefilter = build_prefilter(candidates)

        items: list[dict[str, object]] = []
        counts = {"EXACT": 0, "HIGH": 0, "REVIEW": 0, "UNKNOWN": 0}
        found = 0
        # A popular release is carried by several feeds; listing it once per
        # feed would make the check look like it found twice as much.
        seen_links: set[str] = set()

        feed_targets: list[tuple[str | None, str]] = [(s.name, s.url) for s in sources]
        if extra_url:
            feed_targets.append(('Custom URL', extra_url))
        for source_index, (name, url) in enumerate(feed_targets):
            source_id = sources[source_index].id if source_index < len(sources) else None
            try:
                rss_items: list[RssItem] = effective_fetcher(url)
            except Exception as exc:  # noqa: BLE001 - one broken feed must not abort the run
                if source_id:
                    raise_attention(
                        session,
                        kind=AttentionKind.FEED_FAILED,
                        feed_source_id=source_id,
                        detail=str(exc)[:120],
                    )
                continue

            for rss_item in rss_items:
                if rss_item.link and rss_item.link in seen_links:
                    continue
                if rss_item.link:
                    seen_links.add(rss_item.link)
                found += 1
                if found > _MAX_ITEMS:
                    break
                try:
                    from backend.app.services.release_parser import parse_release_name

                    parsed = parse_release_name(rss_item.title)
                except ValueError:
                    continue
                ranked = analyze_release(parsed, candidates, prefilter=prefilter)
                best = ranked[0] if ranked else None
                classification = best.classification if best else "UNKNOWN"
                counts[classification] = counts.get(classification, 0) + 1
                items.append(
                    {
                        "raw_name": rss_item.title,
                        "torrent_url": rss_item.link,
                        "crc32": parsed.crc32,
                        "season": parsed.season,
                        "episode": parsed.episode,
                        "version": parsed.version,
                        "resolution": parsed.resolution,
                        "subgroup": parsed.subgroup,
                        "media_source": parsed.media_source,
                        "source_name": name,
                        "classification": classification,
                        "confidence": best.confidence if best else 0,
                        "best_anime_id": best.anime_id if best else None,
                        "best_name": best.name if best else None,
                        "candidates": [asdict(c) for c in ranked[:3]],
                    }
                )
        duration_ms = int((time.perf_counter() - started) * 1000)

        run = AnalysisRun(
            duration_ms=duration_ms,
            sources=[s.name for s in sources],
            found=found,
            exact=counts["EXACT"],
            high=counts["HIGH"],
            review=counts["REVIEW"],
            unknown=counts["UNKNOWN"],
        )
        session.add(run)
        session.commit()

        return {
            "run_id": run.id,
            "found": found,
            "exact": counts["EXACT"],
            "high": counts["HIGH"],
            "review": counts["REVIEW"],
            "unknown": counts["UNKNOWN"],
            "duration_ms": duration_ms,
            "items": items,
        }
    finally:
        session.close()


def learn_alias(session: Session, *, anime_id: str, raw_name: str, source: AliasSource) -> str:
    """Store the normalized title component of a release as an alias.

    Never stores the full raw release name - only the parsed title.
    """
    from backend.app.services.release_parser import parse_release_name

    anime = session.get(Anime, anime_id)
    if anime is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Anime not found")
    try:
        parsed = parse_release_name(raw_name)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc
    value = (parsed.title or "").strip()
    if not value:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Release name has no usable title component")

    existing = session.scalar(
        select(AnimeAlias).where(AnimeAlias.anime_id == anime_id, AnimeAlias.value == value)
    )
    if existing is None:
        session.add(AnimeAlias(anime_id=anime_id, value=value[:255], source=source))
    json_aliases = list(anime.aliases or [])
    if value not in json_aliases:
        json_aliases.append(value)
        anime.aliases = json_aliases
    session.commit()
    return value


__all__ = ["learn_alias", "run_analysis"]

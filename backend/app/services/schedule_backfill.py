"""Fill in the airing schedule for anime that predate it.

A migration cannot ask AniList anything, so anime already on the watchlist have
no ``air_slot_at``. The worker walks them a few at a time: AniList rate-limits
(and being a good guest matters more than filling the column this minute), so
this deliberately does a little work per cycle and converges over a few
minutes rather than firing one request per anime at startup.

The same pass refreshes shows whose stored slot has gone stale: ``air_slot_at``
holds the *next* episode's airing instant, so once that episode has aired the
value is in the past. Refreshing keeps the weekday right for a show that moves
slot mid-season.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle

logger = for_component("Schedule")

#: How many anime to look up per pass. AniList allows ~90 requests a minute;
#: this is a background chore, not something anyone is waiting for.
BATCH_SIZE = 5

#: A stored slot older than this is refetched - the next-episode instant it
#: holds has aired, so it no longer says when the show airs next.
STALE_AFTER = timedelta(days=8)


def anime_needing_schedule(session: Session, *, now: datetime | None = None) -> list[Anime]:
    """Anime whose schedule is missing or has gone stale, oldest first.

    Only lifecycles that can still air are considered: a completed or archived
    show's slot never changes again, and asking about it wastes a request.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - STALE_AFTER
    statement = (
        select(Anime)
        .where(
            Anime.lifecycle.in_([AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING]),
            or_(
                Anime.air_slot_at.is_(None) & Anime.air_start_date.is_(None),
                Anime.air_slot_at < cutoff,
            ),
        )
        .order_by(Anime.created_at)
        .limit(BATCH_SIZE)
    )
    return list(session.scalars(statement))


async def refresh_schedules(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Look up the airing schedule for a few anime that are missing it."""
    from backend.app.services.metadata import PROVIDERS, selected

    session = session_factory()
    counts = {"considered": 0, "updated": 0, "unknown": 0, "failed": 0}
    try:
        pending = anime_needing_schedule(session)
        counts["considered"] = len(pending)
        if not pending:
            return counts
        # The selected source, not AniList by name: with Kitsu selected this
        # finds nothing to do (Kitsu publishes no broadcast times), which is
        # the honest outcome rather than quietly asking a source the user did
        # not choose.
        provider = PROVIDERS[await selected(session)]
        for anime in pending:
            if anime.primary_provider != provider.name:
                continue  # another source's id means nothing to this one
            try:
                candidate = await provider.get_anime(anime.primary_provider_id)
            except Exception as exc:  # noqa: BLE001 - a chore must not break the cycle
                # One warning, then stop: whatever stopped the provider
                # answering for this anime will stop it for the next four, and
                # an outage that lasts hours should not write the same line
                # five times a minute for all of them.
                logger.warning(
                    "could not refresh schedules (%s); leaving the rest for the next pass", exc
                )
                counts["failed"] += 1
                break
            if candidate is None:
                counts["failed"] += 1
                continue
            if candidate.air_slot_at is None and candidate.air_start_date is None:
                # The provider knows the show but publishes no schedule for it.
                # Recorded as "asked and got nothing" so the next pass does not
                # keep re-asking: a premiere date of None with a lifecycle that
                # can still air is the one case this cannot distinguish, and
                # one request per cycle is an acceptable price for that.
                counts["unknown"] += 1
                continue
            # A premiere date the user typed in stays theirs (build 152).
            from backend.app.services.metadata_fill import manual_fields

            if "air_start_date" not in manual_fields(anime):
                anime.air_start_date = candidate.air_start_date or anime.air_start_date
            anime.air_slot_at = candidate.air_slot_at or anime.air_slot_at
            counts["updated"] += 1
            logger.info(
                "%s airs %s",
                anime.display_name,
                anime.airs_time and f"weekday {anime.airs_weekday} at {anime.airs_time}"
                or f"weekday {anime.airs_weekday}",
            )
        session.commit()
        return counts
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["BATCH_SIZE", "STALE_AFTER", "anime_needing_schedule", "refresh_schedules"]

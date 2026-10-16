"""Fix everything that is missing, for every anime (build 194).

Asked for after a library came over from Shoko with covers and episode files
missing: *"it goes through all the animes, checks which info is missing and
updates this info."* Manual only - it is a "check when I ask" pass - and it
does the work of the several half-answers that existed before:

* the calendar and the offline id mapping (local, free);
* what Shoko holds: the files, their folder, the library they are in, the
  subgroup and quality they say, and Shoko's own poster;
* a metadata provider, for what is still missing after that - one anime at a
  time and spaced out, like every other pass that asks a free API;
* the episode list, so an anime with a count has its rows.

The one rule it keeps everywhere is **additive**: a value the user set is never
overwritten. An episode count of 12 typed in by hand stays 12; the provider's
own number is still written to the provider's column, so the entry has it and
nothing reads as missing. That is already how ``metadata_fill`` and
``shoko_adopt.enrich`` behave - this only calls them over everything.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle

logger = for_component("Library")

#: What is reported back, at most - a library of hundreds that all failed the
#: same way is one finding, not hundreds of lines.
MAX_PROBLEMS = 40


def _problem(problems: list[str], text: str) -> None:
    if len(problems) < MAX_PROBLEMS:
        problems.append(text)


async def repair_watchlist(
    session_factory: sessionmaker[Session], *, pacer=None
) -> dict[str, object]:
    """Go through every anime and fill in what is missing. Returns what it did."""
    from backend.app.services import libraries, metadata_fill, shoko_adopt
    from backend.app.services.anime import ensure_episode_rows
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.calendar_pacer import Pacer
    from backend.app.services.metadata import selected

    pacer = pacer or Pacer()
    fields: Counter = Counter()
    problems: list[str] = []
    considered = fixed = failed = 0

    session = session_factory()
    try:
        settings = load_app_settings(session)
        shoko_ready = bool(settings.shoko_url and settings.shoko_api_key)
        # Seeding the built-in libraries also points every unassigned anime at
        # the default one; who those were is worked out first so the pass can
        # say it did that.
        had_no_library = {
            anime_id for (anime_id,) in session.execute(select(Anime.id).where(Anime.library_id.is_(None)))
        }
        libraries.ensure_system(session)
        provider_name = await selected(session)

        ids = [
            anime_id
            for (anime_id,) in session.execute(select(Anime.id).order_by(Anime.created_at))
        ]
        for anime_id in ids:
            anime = session.get(Anime, anime_id)
            if anime is None:
                continue
            considered += 1
            filled: list[str] = ["library"] if anime_id in had_no_library else []
            try:
                # 1. Local and free: the offline id list, then the calendar.
                metadata_fill.link_ids(anime)
                filled += metadata_fill.fill_from_calendar(anime)
                from backend.app.services import adult_rule

                if adult_rule.apply(anime):
                    filled.append("adult")

                # 2. What Shoko holds and what the files themselves say. Only
                #    when there is a Shoko to ask; the library fallback and the
                #    AniDB names are local and happen either way.
                if shoko_ready:
                    filled += shoko_adopt.enrich(session, anime, problems=_Cap(problems))
                else:
                    filled += _local_enrich(session, anime, libraries)

                # 3. A provider, for what is still missing - paced, one at a
                #    time, and only when something actually is.
                if (metadata_fill.gaps(anime) or provider_side_missing(anime)) and anime.lifecycle not in (
                    AnimeLifecycle.DROPPED,
                    AnimeLifecycle.ARCHIVED,
                ):
                    await pacer.before(provider_name)
                    try:
                        result = await metadata_fill.refill_one(session, anime)
                    except Exception as exc:  # noqa: BLE001 - one provider miss is not the pass
                        pacer.failed(provider_name)
                        _problem(problems, f"{_name(anime)}: the metadata provider did not answer ({exc})")
                    else:
                        pacer.succeeded(provider_name)
                        if result is not None:
                            filled += list(result.filled)
                    remaining = metadata_fill.gaps(anime)
                    if remaining:
                        _problem(
                            problems,
                            f"{_name(anime)}: still missing {', '.join(remaining)} - no source has it",
                        )

                # 4. The episode list, from the count the entry now has.
                before_rows = len(anime.episodes)
                count = anime.effective_episode_count
                if count:
                    ensure_episode_rows(session, anime, count)
                    session.flush()
                    session.refresh(anime)
                    if len(anime.episodes) > before_rows:
                        filled.append("episodes")

                anime.metadata_checked_at = datetime.now(UTC)
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one anime is not the pass
                session.rollback()
                failed += 1
                logger.warning("could not repair %s: %s", _name(anime), exc)
                _problem(problems, f"{_name(anime)}: could not be repaired ({exc})")
                continue

            if filled:
                fixed += 1
                fields.update(_label(item) for item in filled)
                logger.info("repaired %s: %s", _name(anime), ", ".join(sorted(set(filled))))
    finally:
        session.close()

    return {
        "considered": considered,
        "fixed": fixed,
        "failed": failed,
        "filled": dict(fields),
        "problems": problems,
    }


def provider_side_missing(anime: Anime) -> list[str]:
    """What the *provider's* columns lack while the user's own value covers it.

    The check on the card reads the value in use - the user's when they set one
    - so an anime with a typed-in 12 and nothing from the provider reads as
    complete, and the provider is never asked. But the original is missing from
    the database, which is what was asked to be fixed: *"it should NOT override
    the user 12, but it should add it into the database to not have any missing
    infos."* Asking is safe - the user's value stays, and a different answer is
    raised as a conflict for them to decide (metadata_fill.apply_candidate).
    """
    missing: list[str] = []
    if anime.episode_count is None and anime.episode_count_override is not None:
        missing.append("episode_count")
    if anime.air_slot_at is None and anime.air_slot_override_at is not None:
        missing.append("air_slot")
    return missing


def _local_enrich(session: Session, anime: Anime, libraries) -> list[str]:
    """Without a Shoko: the AniDB names and a library, which need no request."""
    filled: list[str] = []
    if anime.anidb_id is None or not anime.anidb_titles:
        from backend.app.services.anidb_identity import adopt as adopt_names

        if adopt_names(anime) is not None:
            filled.append("anidb")
    if anime.library_id is None:
        default = libraries.default_library(session)
        anime.library_id = default.id
        anime.library = default
        filled.append("library")
    return filled


def _label(item: str) -> str:
    """"12 files" counts as "files"; everything else is its own name."""
    return "files" if item.endswith(" files") else item


def _name(anime: Anime) -> str:
    return anime.display_name or anime.local_name


class _Cap(list):
    """A problems list that stops growing at the cap, whoever appends to it."""

    def __init__(self, target: list[str]) -> None:
        super().__init__()
        self._target = target

    def append(self, text: str) -> None:  # type: ignore[override]
        _problem(self._target, text)

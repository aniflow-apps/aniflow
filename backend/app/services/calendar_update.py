"""Fetching a season and handing it to the snapshot engine (build 142).

The thin part on purpose. Everything that decides anything lives in
``calendar_snapshot`` (what a complete answer is, what may be removed) and
``calendar_identity`` (who a Work is). This module asks the provider, spaces
the asking, and turns whatever comes back - including a refusal - into a
``Snapshot`` for the engine to judge.

That last point is the one worth stating: **a provider failing is not an
exception here.** A timeout becomes a snapshot with an error on it, and the
engine records the failure and changes nothing. Raising instead would leave
the caller to decide what a half-finished update means, and the whole design
exists so that nobody has to.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.db.calendar_store import CalendarStoreBlocked
from backend.app.services import calendar_trace
from backend.app.services.calendar_pacer import Pacer
from backend.app.services.calendar_snapshot import Applied, Snapshot, apply_snapshot

logger = for_component("Calendar")

#: The sources that can actually answer a season request today.
#:
#: AniDB is deliberately absent: it publishes no season listing at all - the
#: dump carries titles and nothing else - so it contributes identity and names
#: and never a membership. The schema allows that; this list is where it shows.
SEASON_PROVIDERS = ("anilist", "kitsu", "jikan", "shikimori", "mal")


@dataclass
class UpdateReport:
    """What one run did, across every provider and season it touched."""

    applied: list[Applied]

    @property
    def added(self) -> int:
        return sum(item.added for item in self.applied)

    @property
    def refused(self) -> list[Applied]:
        return [item for item in self.applied if item.refused]

    def as_text(self) -> str:
        ok = len(self.applied) - len(self.refused)
        return f"{ok} of {len(self.applied)} snapshots applied, {self.added} entries added"


async def fetch_season(provider: str, year: int, season: str, *, pacer: Pacer) -> Snapshot:
    """Ask one provider for one season. Never raises.

    Every failure becomes a snapshot the engine will refuse, which is what
    makes "the provider was down" a recorded fact rather than a stack trace.
    """
    from backend.app.services.calendar_providers import adapter as adapter_for

    adapter = adapter_for(provider)
    if adapter is None:
        # Not a failure, and it must not be counted as one. MyAnimeList
        # without a client id sat at "10 failures in a row" on the debug page
        # next to a provider that was genuinely refusing, which made a
        # configuration choice look like an outage.
        return Snapshot(
            provider=provider,
            year=year,
            season=season,
            skipped="not configured - no credential for this source",
        )

    waited = await pacer.before(provider)
    calendar_trace.record(
        "fetch",
        f"asking {provider} for {season} {year}",
        waited_seconds=round(waited, 1),
    )
    try:
        entries = await adapter.get_season(season, year)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        pacer.failed(provider)
        logger.info("%s could not answer for %s %s: %s", provider, season, year, exc)
        calendar_trace.record(
            "fetch",
            f"{provider} could not answer for {season} {year}",
            level="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return Snapshot(
            provider=provider, year=year, season=season, error=f"{type(exc).__name__}: {exc}"
        )
    pacer.succeeded(provider)
    found = list(entries)
    calendar_trace.record(
        "fetch", f"{provider} answered for {season} {year}", entries=len(found)
    )
    return Snapshot(provider=provider, year=year, season=season, entries=found)


async def load_season(
    year: int,
    season: str,
    *,
    providers: tuple[str, ...] = SEASON_PROVIDERS,
    pacer: Pacer | None = None,
    on_step: object = None,
) -> UpdateReport:
    """Fetch this season from every provider and apply each answer.

    One transaction per provider, not one for the whole run: AniList
    succeeding and Kitsu timing out should leave AniList's work applied. The
    atomicity that matters is per snapshot - half of one provider's season is
    the thing that must never be stored.
    """
    pacer = pacer or Pacer()
    normalized = season.strip().upper()
    report = UpdateReport(applied=[])

    for provider in providers:
        if on_step is not None:
            on_step(provider)  # type: ignore[operator]
        snapshot = await fetch_season(provider, year, normalized, pacer=pacer)
        try:
            sessions = calendar_store.sessions()
        except CalendarStoreBlocked as exc:
            # The gap left open in build 141: a job must not die of this.
            # Nothing can be stored, and saying so once per run is the useful
            # amount of saying so.
            logger.error("calendar update stopped: %s", exc)
            report.applied.append(
                Applied(provider=provider, year=year, season=normalized, refused=str(exc))
            )
            return report

        with sessions() as session:
            try:
                result = apply_snapshot(session, snapshot)
                session.commit()
            except Exception:  # noqa: BLE001 - one provider must not end the run
                session.rollback()
                logger.exception("%s %s %s could not be applied", provider, normalized, year)
                result = Applied(
                    provider=provider,
                    year=year,
                    season=normalized,
                    refused="the snapshot could not be applied and was rolled back",
                )
            report.applied.append(result)

    logger.info("calendar update for %s %s: %s", normalized, year, report.as_text())
    return report

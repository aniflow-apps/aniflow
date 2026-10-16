"""What the Calendar believes about a Work, and who told it (build 142).

Two rules shape this module.

**Canonical values are recomputed from every observation, never patched.**
A provider's answer is written down as an observation; the canonical value on
the Work is then derived from all of them together. So the result does not
depend on which provider happened to speak last, refreshing one source cannot
quietly overwrite a better answer from another, and the same set of
observations always produces the same Work. That is what "do not silently
overwrite critical existing canonical information" has to mean in code.

**One policy per field, not one ranking for everything.** A provider order is
right for a title and wrong for a safety flag. ``is_adult`` is true if *any*
source says so, because a filter that keeps things off the screen has to
believe whichever source noticed; an episode count takes the majority, because
three sources saying twelve and one saying thirteen is not a close call.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.models.calendar import (
    STATUS_UNKNOWN,
    CalendarObservation,
    CalendarPopularity,
    CalendarWork,
)

logger = for_component("Calendar")

#: Which source to believe when a field simply has to pick one.
#:
#: A starting order, as it was in v1, and now testable against the
#: observations rather than argued about: every value a provider gave is kept,
#: so changing this tuple re-derives the calendar without re-downloading it.
ORDER = ("anilist", "mal", "jikan", "kitsu", "shikimori", "anidb")

PRIORITY = "priority"
MAJORITY = "majority"
ANY_TRUE = "any_true"

#: How each canonical field is decided.
POLICIES: dict[str, str] = {
    "kind": PRIORITY,
    # Three sources saying twelve and one saying thirteen is not a close call,
    # and the odd one out is usually counting a recap or a special.
    "episode_count": MAJORITY,
    "air_start_date": PRIORITY,
    "air_slot_at": PRIORITY,
    # Taken from whoever supplied the instant, so the pair stays consistent:
    # an anchor from one source with a time from another is not an anchor.
    "air_slot_episode": PRIORITY,
    "airing_status": PRIORITY,
    # Never by priority. The switch is read as "keep this off my screen".
    "is_adult": ANY_TRUE,
    "duration_minutes": PRIORITY,
    "source_material": PRIORITY,
    "synopsis": PRIORITY,
}

#: Every kind the sources have been seen to use, for the settings page to
#: offer and for the interface to name.
#:
#: Not a closed set - nothing filters on membership of this list, so a kind
#: invented next year appears on a card rather than disappearing from one.
#: Appearing is the safer failure.
#:
#: ``TV_SHORT`` was missing until build 148, which is how it reached the
#: screen as the raw ``TV_SHORT`` with no box in Settings to turn it off.
KNOWN_KINDS: tuple[str, ...] = (
    "TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SPECIAL",
    "MUSIC", "CM", "PV",
)


def normalize_kind(value: object) -> str | None:
    """One spelling for a kind, whichever source supplied it.

    AniList answers ``TV_SHORT``, Kitsu ``ONA``, Jikan ``TV Special`` and both
    MyAnimeList and Shikimori ``tv`` - so a calendar assembled from all five
    showed the same kind three ways depending on which source happened to win
    the field. Reported in build 148: *"it seems some animes have 'movies',
    'ona' 'tv' small lowercase, while the rest is all uppercase"*.

    Applied on the way in and on the way out. On the way in so that two
    sources agreeing are not recorded as a conflict over capitalisation - the
    conflict counts on the NAS were inflated by exactly this - and on the way
    out so that a database filled before build 148 reads correctly without
    being downloaded again.
    """
    if value is None:
        return None
    # Collapsed rather than stripped: "TV Special" and "TV  Special" are one
    # kind, and the underscore is the spelling the rest of AniFlow uses.
    text = "_".join(str(value).split()).upper()
    return text or None


#: Fields where two providers disagreeing is worth a person's attention.
#:
#: Not every field: two synopses differ by definition, and two studio lists
#: differ because one source lists the production committee. These are the
#: ones where a disagreement means somebody is wrong.
WORTH_NOTICING = ("episode_count", "kind", "air_start_date")

_WEEKDAYS = (
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
)


@dataclass(frozen=True)
class Conflict:
    """One field, and the different answers given for it."""

    work_id: str
    field: str
    #: provider -> what that provider said, as text.
    answers: dict[str, str]

    def as_text(self) -> str:
        said = ", ".join(f"{provider} says {value}" for provider, value in sorted(self.answers.items()))
        return f"{self.field}: {said}"


def _encode(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _decode(field: str, raw: str | None) -> object:
    if raw is None or raw == "":
        return None
    if field == "is_adult":
        return raw == "1"
    if field in ("episode_count", "duration_minutes", "air_slot_episode"):
        try:
            return int(raw)
        except ValueError:
            return None
    if field == "air_start_date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    if field == "air_slot_at":
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    if field == "kind":
        # Normalised on the way out as well as in: the observations stored
        # before build 148 carry whatever their source said, and re-deriving
        # the canonical field must not depend on a download having happened
        # since.
        return normalize_kind(raw)
    return raw


def _from_candidate(candidate: AnimeCandidate) -> dict[str, object]:
    """The fields a provider's season entry actually carries.

    Deliberately short. ``duration_minutes`` and ``source_material`` have
    columns and policies and no provider parsing behind them yet - the
    adapters do not extract them. They are left out here rather than written
    as None, so "no observation" keeps meaning "nobody was asked" instead of
    "everybody said nothing".

    ``airing_status`` joined them in build 148, once the adapters had
    something to say. It is written only when a provider actually said
    something: ``UNKNOWN`` is this field's "nobody was asked", and recording
    it as an observation would let one silent source outvote four that spoke.
    """
    fields: dict[str, object] = {
        "kind": normalize_kind(candidate.format),
        "episode_count": candidate.episode_count,
        "air_start_date": candidate.air_start_date,
        "air_slot_at": candidate.air_slot_at,
        "air_slot_episode": candidate.air_slot_episode,
        "is_adult": candidate.is_adult,
        "synopsis": candidate.synopsis,
    }
    if candidate.airing_status and candidate.airing_status != STATUS_UNKNOWN:
        fields["airing_status"] = candidate.airing_status
    return fields


def observe(session: Session, work_id: str, candidate: AnimeCandidate) -> int:
    """Write down what one provider said. Returns how many fields were stored.

    The provider is recorded as the source that was *fetched* - so Jikan and
    MyAnimeList stay apart here even though their ids are one namespace.
    Provenance is about who answered, and they are two different answers that
    happen to agree.
    """
    provider = candidate.provider
    stored = 0
    for field, value in _from_candidate(candidate).items():
        encoded = _encode(value)
        if encoded is None:
            continue
        row = session.scalar(
            select(CalendarObservation).where(
                CalendarObservation.work_id == work_id,
                CalendarObservation.provider == provider,
                CalendarObservation.field == field,
            )
        )
        if row is None:
            session.add(
                CalendarObservation(
                    work_id=work_id, provider=provider, field=field, value=encoded
                )
            )
        else:
            row.value = encoded
        stored += 1

    if candidate.popularity is not None:
        existing = session.scalar(
            select(CalendarPopularity).where(
                CalendarPopularity.work_id == work_id,
                CalendarPopularity.provider == provider,
                CalendarPopularity.metric == "popularity",
            )
        )
        if existing is None:
            session.add(
                CalendarPopularity(
                    work_id=work_id, provider=provider, value=int(candidate.popularity)
                )
            )
        else:
            existing.value = int(candidate.popularity)
    return stored


def _rank(provider: str) -> int:
    return ORDER.index(provider) if provider in ORDER else len(ORDER)


def _decide(field: str, answers: dict[str, object]) -> object:
    """Apply this field's policy to what the providers said."""
    if not answers:
        return None
    policy = POLICIES.get(field, PRIORITY)

    if policy == ANY_TRUE:
        return any(bool(value) for value in answers.values())

    if policy == MAJORITY:
        counted = Counter(str(value) for value in answers.values())
        best, count = counted.most_common(1)[0]
        if count > 1 and count > len(answers) / 2:
            for value in answers.values():
                if str(value) == best:
                    return value
        # No majority: fall through to priority rather than inventing one.

    ordered = sorted(answers.items(), key=lambda item: _rank(item[0]))
    return ordered[0][1]


def assemble(session: Session, work: CalendarWork) -> None:
    """Recompute every canonical field on this Work from its observations.

    Order-independent by construction: the inputs are every observation the
    Work has, so refreshing one provider cannot make the result depend on
    which one was refreshed.
    """
    rows = list(
        session.scalars(
            select(CalendarObservation).where(CalendarObservation.work_id == work.id)
        )
    )
    by_field: dict[str, dict[str, object]] = {}
    for row in rows:
        decoded = _decode(row.field, row.value)
        if decoded is None:
            continue
        by_field.setdefault(row.field, {})[row.provider] = decoded

    for field in POLICIES:
        answers = by_field.get(field, {})
        if not answers:
            continue
        decided = _decide(field, answers)
        if field == "is_adult":
            work.is_adult = bool(decided)
        else:
            setattr(work, field, decided)

    # is_adult is the one field where "nobody said" and "everybody said no"
    # must both come out False rather than being left at whatever it was.
    if "is_adult" not in by_field:
        work.is_adult = False
    if work.airing_status is None:
        work.airing_status = STATUS_UNKNOWN

    # Derived, not observed: the weekday is a reading of the slot, and storing
    # a provider's own spelling of it would be storing the same fact twice.
    work.air_weekday = (
        _WEEKDAYS[work.air_slot_at.weekday()] if work.air_slot_at is not None else None
    )


def conflicts(session: Session, work_id: str) -> list[Conflict]:
    """Where the providers disagree about something that matters.

    The reason observations are kept rather than collapsed. Nothing acts on
    these yet - build 143 decides whether they become Attention entries - but
    a disagreement that is not recorded cannot become anything later.
    """
    rows = list(
        session.scalars(
            select(CalendarObservation).where(
                CalendarObservation.work_id == work_id,
                CalendarObservation.field.in_(WORTH_NOTICING),
            )
        )
    )
    by_field: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.value is None:
            continue
        # Read through the same normaliser the canonical field uses, so
        # "tv" and "TV" are one answer rather than two. Without this, every
        # show two sources agreed about counted as a conflict.
        value = normalize_kind(row.value) if row.field == "kind" else row.value
        if value is None:
            continue
        by_field.setdefault(row.field, {})[row.provider] = value
    return [
        Conflict(work_id=work_id, field=field, answers=answers)
        for field, answers in sorted(by_field.items())
        if len(set(answers.values())) > 1
    ]


def score_popularity(session: Session) -> int:
    """Give every Work a comparable number to sort by. Returns how many.

    The problem: AniList counts lists, MyAnimeList counts members, and the two
    numbers are not on the same scale - adding them, as v1 did, produces a
    quantity with no unit.

    So each provider's value is scaled against the largest value that provider
    has been seen to give, on a log scale because popularity is distributed
    that way - the top show has orders of magnitude more than the median, and
    a linear scale would leave everything below the top few indistinguishable.
    A Work's score is the best of its providers' scaled values.

    Deliberately re-derivable: the raw numbers stay in ``calendar_popularity``,
    so this whole function can be replaced later without re-downloading
    anything. And it is not what the page displays - the five-star rating is
    computed per season at display time, which is what stops a quiet season
    from looking uniformly unpopular.
    """
    ceilings = {
        provider: value
        for provider, value in session.execute(
            select(CalendarPopularity.provider, func.max(CalendarPopularity.value)).group_by(
                CalendarPopularity.provider
            )
        )
    }
    if not ceilings:
        return 0

    best: dict[str, float] = {}
    for row in session.scalars(select(CalendarPopularity)):
        ceiling = ceilings.get(row.provider) or 0
        if ceiling <= 0 or row.value <= 0:
            continue
        scaled = math.log1p(row.value) / math.log1p(ceiling)
        if scaled > best.get(row.work_id, 0.0):
            best[row.work_id] = scaled

    changed = 0
    for work in session.scalars(select(CalendarWork)):
        # A Work nobody counts still needs a defined, sortable value - it
        # sorts last rather than sorting randomly.
        value = round(best.get(work.id, 0.0), 6)
        if work.popularity_score != value:
            work.popularity_score = value
            changed += 1
    return changed

"""The Calendar's own schema (build 141, Calendar v2).

Replaces the per-source tables in ``calendar_sources.py``, which stored one
row per source per show per season and merged them on the way out. That shape
answered "where do the sources disagree" - the question the experiment was
built to ask - and it is the wrong shape for a calendar that has to be the
source of truth for a web page and, later, a television client.

The difference in one line: **a Work exists here in its own right**, with an
identity that does not change, and providers contribute observations to it.

Two kinds of data live in this file and they are not equally valuable:

* **Irreplaceable.** ``calendar_work.id`` and ``calendar_identity`` - the
  identity spine - plus ``calendar_alias`` (merge decisions), the evidence a
  link was proven on, and inactive rows in ``calendar_membership``, which are
  the memory that a provider *used to* place a show in a season. No provider
  can give any of that back.
* **Re-derivable.** Active memberships, canonical metadata, observations,
  artwork, popularity. Downloading it again costs API calls and nothing else.

That distinction is why this file is migrated from build 141 onward and never
deleted to fix a schema mismatch - see ``db/calendar_store.py``.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.calendar_store import CalendarBase
from backend.app.db.types import UtcDateTime, now_utc
# Re-exported, not redeclared: every existing `from models.calendar import
# STATUS_...` keeps working, and there is one definition to change.
from backend.app.integrations.metadata.airing import (  # noqa: F401
    AIRING_STATUSES,
    STATUS_AIRING,
    STATUS_CANCELLED,
    STATUS_FINISHED,
    STATUS_NOT_YET_AIRED,
    STATUS_UNKNOWN,
)

#: How a provider id came to be attached to a Work, strongest first.
#:
#: The distinction is load-bearing rather than descriptive: a link may only
#: mint a canonical id, or merge two Works, when it was *proven*. A title
#: match never qualifies - see ``PROVEN`` below.
EVIDENCE_SELF = "SELF"  # the provider that issued the id described this entry
EVIDENCE_CROSS = "CROSS_REFERENCE"  # another provider published this id
EVIDENCE_SHOKO = "SHOKO"  # Shoko Server's own mapping
EVIDENCE_USER = "USER"  # a person confirmed it
EVIDENCE_TITLE = "TITLE"  # matched by name alone
#: Found in the offline id-mapping list (build 154) - see services/id_mapping.py.
#: The list is compiled from the providers' own cross-links, so it is the same
#: kind of evidence as a cross-reference, gathered in one file instead of
#: from five APIs.
EVIDENCE_MAPPING = "MAPPING"

#: Evidence strong enough to act on.
#:
#: AniDB is the reason this set exists. It is the most useful identity in the
#: wider AniFlow/Shoko ecosystem and the hardest to establish: the published
#: dump carries titles and nothing else, so an AniList-to-AniDB link derived
#: from it is a *title* match wearing an id's clothes. Acting on those would
#: merge shows that share a name, which is the one mistake in here that cannot
#: be undone by downloading something again.
PROVEN = frozenset({EVIDENCE_SELF, EVIDENCE_CROSS, EVIDENCE_SHOKO, EVIDENCE_USER, EVIDENCE_MAPPING})

#: What a Work's airing state normalizes to. Providers spell these a dozen
#: ways; the calendar stores one of these five.
#:
#: Declared in ``integrations/metadata/airing.py``, which is where a
#: provider's word is turned into one of them, and re-exported here so that
#: the database's vocabulary and the adapters' cannot drift apart (build 148).
#: That module imports nothing, which is what makes depending on it safe.

#: Title kinds. ``SYNONYM`` is the open end: providers publish alternates that
#: fit none of the others, and they are worth keeping for matching.
TITLE_MAIN = "MAIN"
TITLE_ENGLISH = "ENGLISH"
TITLE_ROMAJI = "ROMAJI"
TITLE_NATIVE = "NATIVE"
TITLE_SYNONYM = "SYNONYM"


class CalendarWork(CalendarBase):
    """One anime, with a public identity that never changes.

    ``id`` is the whole point of this table. It is deterministic and readable
    (``al54``, ``adb2342``, ``mal13``), it is minted once from the best
    identity proven at the time, and it is then permanent - a later AniDB id
    attaches as an identity and does not rename the Work.

    That rule exists because the id is an external contract. A television
    client holding ``adb2342`` is broken by a renamed Work exactly as badly as
    by a deleted database, and a canonical id that improves itself would churn
    hardest on the upcoming season, which is the page people actually look at.

    The canonical metadata sits on this row rather than in a key-value table
    because drawing a season reads all of it for every Work in that season,
    and that is the only query in the Calendar that has to be quick.
    """

    __tablename__ = "calendar_work"

    #: ``al54``. A provider prefix and that provider's id - see
    #: ``services/calendar_identity.py`` for the registry, which is permanent.
    id: Mapped[str] = mapped_column(String(40), primary_key=True)

    #: Which identity the id was minted from. Kept so a rebuild can verify it
    #: would mint the same one, rather than trusting that it did.
    id_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    id_provider_id: Mapped[str] = mapped_column(String(80), nullable=False)

    #: For logs. Not a display title - that comes from ``calendar_title``,
    #: which knows about languages.
    label: Mapped[str] = mapped_column(String(400), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)

    # --- canonical metadata ------------------------------------------------
    # Assembled per field with a declared policy, not one global provider
    # ranking - see services/calendar_metadata.py. Every value here also
    # exists as a row in calendar_observation, so a canonical value can always
    # be traced to who said it and compared with who disagreed.

    kind: Mapped[str | None] = mapped_column(String(40))
    episode_count: Mapped[int | None] = mapped_column(Integer)
    air_start_date: Mapped[date | None] = mapped_column(Date)
    #: One instant an episode airs, where a provider publishes one. AniList is
    #: currently the only one that does.
    air_slot_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Which episode that instant belongs to (build 146). The anchor the rest
    #: of the schedule is counted from - see services/calendar_schedule.py.
    air_slot_episode: Mapped[int | None] = mapped_column(Integer)
    #: ``WEDNESDAY``. Normalized, because providers write Wed, Wednesday and
    #: 3 for the same day and those are not three facts.
    air_weekday: Mapped[str | None] = mapped_column(String(12))
    airing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_UNKNOWN
    )

    #: True if *any* source says so, never by provider priority. A filter that
    #: keeps things off the screen has to believe whichever source noticed.
    is_adult: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    source_material: Mapped[str | None] = mapped_column(String(40))
    synopsis: Mapped[str | None] = mapped_column(Text)

    #: AniFlow's own number, derived from the per-provider observations in
    #: ``calendar_popularity`` because those are not comparable with each
    #: other. Used for sorting and as the defined fallback for a Work no
    #: provider counts; the five-star display is computed per season at
    #: display time and does not read this.
    popularity_score: Mapped[float | None] = mapped_column(Float)


class CalendarIdentity(CalendarBase):
    """Every provider id known for a Work, and how it was established.

    A provider id belongs to exactly one Work: the unique constraint is what
    makes "this entry is one we already have" a lookup rather than a judgement.
    """

    __tablename__ = "calendar_identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_identity_provider"),
        Index("ix_identity_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(80), nullable=False)

    #: One of the EVIDENCE_* values. ``PROVEN`` membership decides whether
    #: this link may mint a canonical id or merge two Works.
    evidence: Mapped[str] = mapped_column(String(20), nullable=False, default=EVIDENCE_SELF)
    #: Who said so - the provider whose response carried the cross-reference,
    #: or ``shoko``/``user``. Null for EVIDENCE_SELF, where it would repeat
    #: ``provider``.
    evidence_from: Mapped[str | None] = mapped_column(String(40))
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)


class CalendarAlias(CalendarBase):
    """A public id that now resolves to a different Work.

    Only merges create these: two Works that turn out to be one. There is no
    other way for a canonical id to stop being current, because ids are never
    re-minted.

    Never removed. A client that stored the old id years ago must still be
    able to ask for it.
    """

    __tablename__ = "calendar_alias"

    alias_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    merged_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)
    #: The evidence the merge was made on, so an incorrect one can be found
    #: again rather than guessed at.
    evidence: Mapped[str] = mapped_column(String(20), nullable=False, default=EVIDENCE_USER)


class CalendarTitle(CalendarBase):
    """Every name a Work goes by, kept apart rather than collapsed.

    Providers disagree about names in ways that matter: matching a release
    filename wants romaji, a person reading the page wants their own language,
    and the AniDB dump carries dozens of synonyms that are the best matching
    material in the application. One string would throw all of that away.
    """

    __tablename__ = "calendar_title"
    __table_args__ = (
        UniqueConstraint("work_id", "provider", "kind", "language", name="uq_title_slot"),
        Index("ix_title_norm", "title_norm"),
        Index("ix_title_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: One of the TITLE_* values.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=TITLE_MAIN)
    #: BCP-ish, as the provider gives it: ``en``, ``ja``, ``de``. Null where a
    #: provider does not say, which romaji usually does not.
    language: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    #: Folded for matching. Indexed, because matching by name is the fallback
    #: path and it runs over every title in the file.
    title_norm: Mapped[str] = mapped_column(String(400), nullable=False)


class CalendarSeason(CalendarBase):
    """A season, and when it was last asked about.

    Two timestamps rather than a state machine: whether a season is stale is a
    question about *now*, and storing the answer means storing something that
    goes out of date by itself. Whether one is being fetched at this moment is
    runtime state and lives with the job, not here.
    """

    __tablename__ = "calendar_season"
    __table_args__ = (UniqueConstraint("year", "season", name="uq_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)

    last_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Null means "never successfully loaded", which is what makes a season
    #: fetch on demand when somebody navigates to it.
    last_success_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class CalendarMembership(CalendarBase):
    """One provider saying a Work belongs to a season.

    Per provider, deliberately: providers disagree about which season a show
    belongs to, and for a show that has not aired yet they disagree often. A
    single canonical season would have to pick a winner every time, and the
    disagreement is information.

    A provider may add, keep, move or deactivate **its own** rows and no
    others', and only a validated complete snapshot may deactivate anything -
    see ``services/calendar_snapshot.py``.
    """

    __tablename__ = "calendar_membership"
    __table_args__ = (
        UniqueConstraint("season_id", "provider", "provider_id", name="uq_membership_entry"),
        Index("ix_membership_season_work", "season_id", "work_id"),
        Index("ix_membership_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, nullable=False)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(80), nullable=False)

    first_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)
    #: False once a complete snapshot stopped listing it. The row stays: it is
    #: the memory that this provider once placed this show in this season, and
    #: nothing can reconstruct that afterwards.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CalendarGenre(CalendarBase):
    """A genre, once, under a normalized name."""

    __tablename__ = "calendar_genre"
    __table_args__ = (UniqueConstraint("name", name="uq_genre_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: ``Slice of Life``, as it should be shown.
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Folded, for matching a provider's spelling onto this row.
    name_norm: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    #: True for the genres the adult filter covers beyond ``is_adult`` -
    #: ecchi, hentai, erotica. A column rather than a list in the frontend,
    #: because the filter and the hidden-count must read one answer.
    adult: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CalendarWorkGenre(CalendarBase):
    __tablename__ = "calendar_work_genre"
    __table_args__ = (UniqueConstraint("work_id", "genre_id", name="uq_work_genre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    genre_id: Mapped[int] = mapped_column(Integer, nullable=False)


class CalendarStudio(CalendarBase):
    __tablename__ = "calendar_studio"
    __table_args__ = (UniqueConstraint("name_norm", name="uq_studio_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(200), nullable=False)


class CalendarWorkStudio(CalendarBase):
    __tablename__ = "calendar_work_studio"
    __table_args__ = (UniqueConstraint("work_id", "studio_id", name="uq_work_studio"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    studio_id: Mapped[int] = mapped_column(Integer, nullable=False)


class CalendarArtwork(CalendarBase):
    """One poster per Work, and where it came from.

    One, not one per provider. The per-source copies in v1 existed so the
    sources could be compared on their artwork; the calendar itself only ever
    draws one, and downloading five to show one is four downloads and five
    files for no gain.

    Provenance stays because "why is this show's poster the wrong one" is a
    real question with a findable answer.
    """

    __tablename__ = "calendar_artwork"

    work_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    #: Filename under ``<data>/calendar-art/``. Null while only the URL is
    #: known, which is how a season can be listed before its art has landed.
    file: Mapped[str | None] = mapped_column(String(200))
    chosen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)


class CalendarObservation(CalendarBase):
    """What one provider said about one field of one Work.

    Every canonical value on ``calendar_work`` has its sources here. That is
    what makes a disagreement - AniList saying twelve episodes and MAL saying
    thirteen - detectable rather than merely resolved, and it is why a
    canonical value is never silently overwritten by the next provider to
    speak.

    Values are stored as text. The canonical column carries the type; this
    table carries the claim.
    """

    __tablename__ = "calendar_observation"
    __table_args__ = (
        UniqueConstraint("work_id", "provider", "field", name="uq_observation_slot"),
        Index("ix_observation_work_field", "work_id", "field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)


class CalendarPopularity(CalendarBase):
    """One provider's popularity metric, kept raw.

    Raw because the numbers are not comparable: AniList counts lists, MAL
    counts members, and adding them produces a quantity with no unit. The
    normalized ``calendar_work.popularity_score`` is derived from these, and
    keeping the originals is what allows that derivation to be changed later
    without re-downloading anything.
    """

    __tablename__ = "calendar_popularity"
    __table_args__ = (
        UniqueConstraint("work_id", "provider", "metric", name="uq_popularity_slot"),
        Index("ix_popularity_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``popularity``, ``members``, ``favourites`` - what the provider counted.
    metric: Mapped[str] = mapped_column(String(40), nullable=False, default="popularity")
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=now_utc)


class CalendarRelation(CalendarBase):
    """How two Works are related, as a provider describes it.

    Filed as nice-to-have and built now, because the sequel/gap/prequel mark
    on the calendar is currently derived by reading "Season 2" out of a title
    string and comparing names. That works until it does not, and this is what
    replaces it.
    """

    __tablename__ = "calendar_relation"
    __table_args__ = (
        UniqueConstraint("work_id", "related_work_id", "kind", name="uq_relation"),
        Index("ix_relation_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    related_work_id: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``PREQUEL``, ``SEQUEL``, ``SIDE_STORY``, ``PARENT``, ``SUMMARY``, ...
    #: An open set: a relation nobody modelled is better stored than dropped.
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)


#: The key under which the first fill is remembered. See `CalendarNote`.
NOTE_FIRST_FILL = "first_fill_done"


class CalendarNote(CalendarBase):
    """Things the calendar remembers about itself (build 148).

    One row per fact, keyed by name, because the alternative - a column on a
    single-row table - needs a migration for each new fact and this does not.

    It lives in ``calendar.db`` rather than in the settings file on purpose.
    The one fact it holds today is whether the first fill has been done, and
    the honest answer to that question is a property of *this calendar
    database*: reset the calendar and the first fill is worth offering again,
    which is exactly what happens when the file it was written in goes with
    it. A flag in ``aniflow.yaml`` would have survived the reset and quietly
    refused the one thing a fresh installation wants.
    """

    __tablename__ = "calendar_note"
    __table_args__ = (UniqueConstraint("key", name="uq_calendar_note"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    written_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc)


class CalendarSourceState(CalendarBase):
    """When each provider was last asked, and what happened.

    Per provider rather than per provider-and-season: this answers "is AniList
    working", which is what a status line and the pacer need. Per-season
    timing lives on ``calendar_season``.
    """

    __tablename__ = "calendar_source_state"
    __table_args__ = (UniqueConstraint("provider", name="uq_source_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(400))
    #: Consecutive failures, which is what the pacer backs off on.
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CalendarIdMap(CalendarBase):
    """One anime's ids across the databases, from the offline mapping list.

    Build 154, asked for as: *"the data in the calendar.db needs to have ALL
    ids listed ... So that there is never a case, where anime x can not link
    to anime x."*  The providers only half-answer that: AniList names a MAL
    id and nothing else, AniDB ids arrived through Kitsu or as title guesses.
    This table is the whole answer, read from one weekly file.

    Re-derivable - the file is downloaded again - and deliberately not the
    identity spine. A row here does not make a Work; it is what the spine
    consults when a Work is made or looked up. See services/id_mapping.py.
    """

    __tablename__ = "calendar_id_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anidb: Mapped[str | None] = mapped_column(String(20), index=True)
    anilist: Mapped[str | None] = mapped_column(String(20), index=True)
    mal: Mapped[str | None] = mapped_column(String(20), index=True)
    kitsu: Mapped[str | None] = mapped_column(String(20), index=True)


class CalendarIdMapState(CalendarBase):
    """When the mapping list was last fetched, and how much it held. One row."""

    __tablename__ = "calendar_id_map_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    downloaded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    attempted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(400))
    #: When every existing Work was last given the ids the list knows - done
    #: once after each new list, not on every pass.
    linked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


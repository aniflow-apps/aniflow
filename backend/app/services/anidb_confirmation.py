"""Is this anime's AniDB id certain, and if not, what could it be? (build 229)

An anime's AniDB id is what Shoko states, what numbering walks chains by and
what links two catalogues to one show - and it is often found by *name*: the
AniDB title dump maps names to ids, and two shows that share a name (Anemone
2011 and Anemone (2027)) are one wrong guess apart. A guess nobody checked
looked exactly like a certainty, so nothing could say which ids to double-check.

Three answers, all derived - nothing about *how* an id was found is stored, so
there is no schema change:

``confirmed``
    The offline id list agrees (it gives this AniDB id to a show with one of
    this anime's own ids), or the user vouched for exactly this id.
``unconfirmed``
    Found by name, and nothing else vouches for it. Not wrong - right most of
    the time - but not known.
``contradicted``
    The id list gives this AniDB id to a *different* show, or gives this show
    a different AniDB id. Almost always a real mistake; occasionally the list
    splitting one AniDB entry in two, which only a person can tell.
``unknown``
    Nothing to compare with (no AniDB id, or the id list has not been loaded).
    No flag: an absence of evidence is not a reason to nag.

The user's word is kept in ``metadata_json["anidb_confirmed"]`` - the id they
vouched for, not a flag, so changing the id un-confirms it by itself.
"""
from dataclasses import dataclass, field

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime
from backend.app.services import anidb_titles

logger = for_component("AniDB")

#: Inside ``Anime.metadata_json``: the AniDB id the user checked and kept.
CONFIRMED_KEY = "anidb_confirmed"

CONFIRMED = "confirmed"
UNCONFIRMED = "unconfirmed"
CONTRADICTED = "contradicted"
UNKNOWN = "unknown"

#: How many candidates the chooser offers.
CANDIDATE_LIMIT = 12


@dataclass(frozen=True)
class Verdict:
    state: str
    #: One sentence for the person: why it is (un)certain.
    reason: str = ""
    #: ``list`` (the id list agrees) or ``you`` - only for ``confirmed``.
    by: str | None = None


@dataclass
class Candidate:
    """One AniDB entry the anime might be."""

    aid: int
    title: str
    #: A few of its other names, so two entries with one title can be told apart.
    names: list[str] = field(default_factory=list)
    #: The id list says this entry is one of this anime's own ids.
    agrees: bool = False
    #: The id list gives this entry to another show: ``{"anilist": "10516", ...}``.
    belongs_to: dict[str, str] = field(default_factory=dict)
    current: bool = False


def stored_id(anime: Anime) -> int | None:
    """The AniDB id the anime holds, from the column or the provider map."""
    value = anime.anidb_id or (anime.provider_ids or {}).get("anidb")
    return int(value) if str(value or "").isdigit() and int(value) > 0 else None


def _own_ids(anime: Anime) -> dict[str, str]:
    """This anime's ids that name one show, apart from the AniDB one under suspicion."""
    from backend.app.services.library_flags import _show_ids

    ids = _show_ids((anime.primary_provider, str(anime.primary_provider_id)), anime.provider_ids)
    ids.pop("anidb", None)
    return ids


def verdict(anime: Anime) -> Verdict:
    """How certain this anime's AniDB id is. Never raises: a verdict is a hint."""
    aid = stored_id(anime)
    if aid is None:
        return Verdict(UNKNOWN)
    raw = anime.metadata_json if isinstance(anime.metadata_json, dict) else {}
    if raw.get(CONFIRMED_KEY) == aid:
        return Verdict(CONFIRMED, "You checked this id.", by="you")
    try:
        return _from_the_list(anime, aid)
    except Exception:  # noqa: BLE001 - a calendar store that is missing or busy is not an error to show
        logger.debug("AniDB id %s could not be checked against the id list", aid, exc_info=True)
        return Verdict(UNKNOWN)


def _from_the_list(anime: Anime, aid: int) -> Verdict:
    from backend.app.db import calendar_store
    from backend.app.services import id_mapping

    if not calendar_store.exists() or not id_mapping.status().rows:
        return Verdict(UNKNOWN)
    own = _own_ids(anime)
    with calendar_store.sessions()() as session:
        reachable = id_mapping.ids_for_any(session, own) if own else {}
        owner = id_mapping.ids_for(session, "anidb", str(aid))
    listed = reachable.get("anidb")
    if listed is not None:
        if listed == str(aid):
            return Verdict(CONFIRMED, "The id list agrees.", by="list")
        return Verdict(
            CONTRADICTED,
            f"The id list says this show's AniDB id is {listed}, not {aid}.",
        )
    if owner:
        differing = [f"{kind} {value}" for kind, value in own.items() if owner.get(kind) not in (None, value)]
        if differing:
            elsewhere = ", ".join(f"{kind} {value}" for kind, value in owner.items() if kind != "anidb")
            return Verdict(
                CONTRADICTED,
                f"The id list gives AniDB {aid} to another show ({elsewhere}), not to this one ({', '.join(differing)}).",
            )
        if any(owner.get(kind) == value for kind, value in own.items()):
            return Verdict(CONFIRMED, "The id list agrees.", by="list")
    return Verdict(UNCONFIRMED, "Found by name; the id list does not know this id, so nothing else vouches for it.")


def candidates(anime: Anime, limit: int = CANDIDATE_LIMIT) -> list[Candidate]:
    """The AniDB entries this anime might be: every one whose name matches any name it has.

    The AniDB title dump has names and no years, so nothing here can say
    "2011 or 2027". What it can say, from the id list, is which entries
    already belong to some *other* show - those are kept in the list (the list
    can be the one that is wrong) but marked, and sorted last.
    """
    from backend.app.services.anidb_identity import titles_of
    from backend.app.services.library_flags import without_year

    current = stored_id(anime)
    found: list[int] = [current] if current else []
    seen = set(found)
    for name in titles_of(anime):
        if not name:
            continue
        for query in dict.fromkeys((str(name), without_year(str(name)))):
            for aid in anidb_titles.search(query, limit=6):
                if aid not in seen:
                    seen.add(aid)
                    found.append(aid)
    found = found[: limit * 2]

    own = _own_ids(anime)
    owners = _owners(found)
    result: list[Candidate] = []
    for aid in found:
        owner = {kind: value for kind, value in owners.get(aid, {}).items() if kind != "anidb"}
        agrees = any(owner.get(kind) == value for kind, value in own.items())
        other = bool(owner) and not agrees and any(owner.get(kind) not in (None, value) for kind, value in own.items())
        names = anidb_titles.names_for(aid) or {}
        title = anidb_titles.primary_title(aid) or names.get("main") or f"AniDB {aid}"
        result.append(
            Candidate(
                aid=aid,
                title=title,
                names=[value for key, value in names.items() if value and value != title][:3],
                agrees=agrees,
                belongs_to=owner if other else {},
                current=aid == current,
            )
        )
    # Vouched-for first, then the one it has now, then the rest in the order
    # the dump ranked them, and the ones that belong to someone else last.
    order = {candidate.aid: position for position, candidate in enumerate(result)}
    result.sort(key=lambda c: (bool(c.belongs_to), not c.agrees, not c.current, order[c.aid]))
    return result[:limit]


def _owners(aids: list[int]) -> dict[int, dict[str, str]]:
    """What the id list knows about each of these AniDB ids. Empty when it cannot be asked."""
    from backend.app.db import calendar_store
    from backend.app.services import id_mapping

    if not aids or not calendar_store.exists():
        return {}
    try:
        with calendar_store.sessions()() as session:
            return {aid: id_mapping.ids_for(session, "anidb", str(aid)) for aid in aids}
    except Exception:  # noqa: BLE001
        return {}


def confirm(anime: Anime, aid: int) -> None:
    """Give the anime this AniDB id and record that a person vouched for it.

    The id and AniDB's own names go in through ``adopt`` (which writes the
    column and the provider map and fills the names from the dump); the
    confirmation is the id itself, so a later change of id un-confirms it.
    """
    from backend.app.services.anidb_identity import adopt

    adopt(anime, int(aid))
    raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
    raw[CONFIRMED_KEY] = int(aid)
    anime.metadata_json = raw  # reassigned: SQLAlchemy does not see a change inside a JSON dict

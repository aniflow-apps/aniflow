"""Which of an anime's titles to use, and in what order.

A provider gives several names for the same show - English, romaji, the native
title, and a list of synonyms - and AniFlow has two quite different jobs that
have to choose between them:

``folder_order``
    What to call the folder. This one is a matter of taste: some people want
    ``Iron Wok Jan!`` on disk, others ``Tetsunabe no Jan!``. It only applies
    where AniFlow names the folder at all - standalone mode, and the hand-over
    folder in Shoko Server mode. Shoko names the library itself, so this
    setting does not reach it.

``search_order``
    What to ask an indexer for. Not taste at all: it decides whether anything
    is found. Release groups overwhelmingly use romaji, so that is the default
    first choice, and English is often a title no tracker has ever used.

``display_order``
    What to *call* it on screen - the Calendar, the Watchlist, everywhere a
    name is shown (build 85). Taste again, and separate from the folder order
    on purpose: plenty of people want ``Shingeki no Kyojin`` in the interface
    and ``Attack on Titan`` on disk, or the other way round, and before this
    the screen simply showed whatever the metadata source happened to call the
    show. It changes no data and no filename - only what is drawn.

Stored as ordered lists rather than a single choice, because every one of them
is missing for some anime - a show with no English title still needs a folder.
The order says "this, and if it is empty, that".

Both are lists of the same four names, so one vocabulary serves both and the
UI is the same control twice.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The kinds of title AniFlow knows about, and where each comes from.
ENGLISH = "english"
ROMAJI = "romaji"
ALTERNATIVE = "alternative"
JAPANESE = "japanese"

KINDS = (ENGLISH, ROMAJI, ALTERNATIVE, JAPANESE)

#: Romaji first for searching: release groups name files that way, and asking a
#: tracker for an English title it has never used finds nothing.
DEFAULT_SEARCH_ORDER = (ROMAJI, ENGLISH, ALTERNATIVE, JAPANESE)

#: English first for folders, and deliberately not the same as the search
#: order: the two answer different questions. A folder is read by a person
#: browsing their library, and most people would rather see "BLEACH
#: Thousand-Year Blood War - The Calamity" there while still *searching* for
#: "BLEACH Sennen Kessen-hen". Taste, so it is only a default (build 70).
DEFAULT_FOLDER_ORDER = (ENGLISH, ROMAJI, ALTERNATIVE, JAPANESE)

#: English first on screen, which is what most people reading an interface in
#: English expect. Japanese last for the same reason - not because it is worth
#: less, but because a name in a script you cannot read is not a name you can
#: find a show by.
DEFAULT_DISPLAY_ORDER = (ENGLISH, ROMAJI, ALTERNATIVE, JAPANESE)


@dataclass(frozen=True)
class Titles:
    """One anime's names, in the shape this module works with."""

    english: str | None = None
    romaji: str | None = None
    alternative: str | None = None
    japanese: str | None = None
    #: The name to fall back on when every preference is empty. The folder
    #: name the user approved when adding the anime, which beats the provider's
    #: display name precisely because a person chose it.
    display: str = ""

    def get(self, kind: str) -> str | None:
        return {
            ENGLISH: self.english,
            ROMAJI: self.romaji,
            ALTERNATIVE: self.alternative,
            JAPANESE: self.japanese,
        }.get(kind)


def from_anime(anime) -> Titles:
    """The titles of a stored anime, or of a provider candidate.

    Both carry the same field names, so one reader serves the add flow and
    everything afterwards.
    """
    aliases = list(getattr(anime, "aliases", None) or [])
    return Titles(
        english=_clean(getattr(anime, "title_english", None)),
        romaji=_clean(getattr(anime, "title_romaji", None)),
        alternative=_clean(aliases[0] if aliases else None),
        japanese=_clean(getattr(anime, "title_native", None)),
        # local_name first: it is what the user typed in the setup dialog, and
        # an explicit choice outranks anything derived.
        display=_clean(getattr(anime, "local_name", None))
        or _clean(getattr(anime, "display_name", None))
        or "",
    )


def parse_order(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    """One stored order, made safe to use.

    Hand-editable configuration, so it is treated as a suggestion: unknown
    names are dropped, duplicates collapse, and anything the user left out is
    appended in the default order rather than being unavailable. The result
    always contains every kind exactly once, so a lookup can never run out of
    options because of a typo.
    """
    wanted: list[str] = []
    raw = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    for item in raw:
        name = str(item).strip().lower()
        if name in KINDS and name not in wanted:
            wanted.append(name)
    for name in default:
        if name not in wanted:
            wanted.append(name)
    return tuple(wanted)


def pick(titles: Titles, order: tuple[str, ...]) -> str:
    """The first title the order asks for that this anime actually has."""
    for kind in order:
        value = titles.get(kind)
        if value:
            return value
    return titles.display


def display_order() -> tuple[str, ...]:
    """The configured order for names on screen.

    Read once per request rather than once per anime: a watchlist is two
    hundred rows and this is a settings lookup, not a per-row decision.
    """
    try:
        from backend.app.services.app_settings import load_app_settings

        stored = getattr(load_app_settings(), "title_display_order", "")
    except Exception:  # noqa: BLE001 - a name on screen must not need a file
        stored = ""
    return parse_order(stored, DEFAULT_DISPLAY_ORDER)


def display_name(anime, order: tuple[str, ...] | None = None) -> str:
    """What to call this anime on screen.

    Takes a stored anime or a provider candidate - both carry the same title
    fields - so the Calendar and the Watchlist answer the question the same
    way, which is the point of having the setting at all.
    """
    return pick(from_anime(anime), order or display_order())


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


__all__ = [
    "ALTERNATIVE",
    "DEFAULT_FOLDER_ORDER",
    "DEFAULT_SEARCH_ORDER",
    "ENGLISH",
    "JAPANESE",
    "KINDS",
    "ROMAJI",
    "Titles",
    "from_anime",
    "parse_order",
    "pick",
]

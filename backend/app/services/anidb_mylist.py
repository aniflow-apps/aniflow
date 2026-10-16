"""AniDB's My List, from the export file the user uploads (build 244).

AniFlow never asks AniDB (rule: the title dump only). The user exports their My List from AniDB's own
Export page - a ``.tgz`` of CSV files, built in the background by AniDB - and uploads it here. The archive
holds ``mylist.csv`` (one line per anime) and ``anime/a<aid>.csv`` (every episode of that anime with
``myState`` 1 when it is in the list).

What the numbers on AniDB's list page mean, checked against the first reference list (21 entries, 20 of
them in the file):

* ``held / total + specials`` = regular episodes in the list / the anime's regular episodes (``Eps`` in
  ``mylist.csv``) + **specials in the list**;
* **incomplete** = something is in the list, and fewer regular episodes than the anime has - an anime
  with nothing in the list at all is not "incomplete", it is only on the account (Chobits, 0 of 26);
* **adult** = ``isAdult``; **type** = ``TypeName``.

The export carries no airing status. It is *estimated* (``airing_state``): a year range that ended before
this year is finished; otherwise the last episode in the list plus a week for each one still missing.
That is right for weekly shows and wrong for irregular releases (an OVA series released over years), which
is why the page says it is an estimate.

The parsed list is kept as one JSON file beside the databases, so there is no migration - and so is the uploaded
archive itself (``anidb-export.tgz``, build 259), so a reader that learns more later (``PARSER_VERSION``) runs over
the file again by itself instead of asking for a second upload.
"""
from __future__ import annotations

import io
import json
import re
import tarfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from backend.app.core.config import get_settings

#: Guards against an archive that is not what it says: a tar can claim far more than it holds.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 50_000
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 800 * 1024 * 1024

TYPES = ("movie", "musicvideo", "other", "ova", "tv", "tvspecial", "unknown", "web")
ADULT = ("ignore", "hide", "only")
AIRING = ("ignore", "airing", "finished", "unaired")
STATUS = ("ignore", "complete", "incomplete")

_lock = threading.Lock()


class ExportError(ValueError):
    """The file is not an AniDB My List export (or not one AniFlow can read)."""


@dataclass
class Entry:
    aid: int
    title: str
    english: str | None
    year: str
    type: str
    adult: bool
    #: Regular episodes the anime has, and specials it has.
    total: int
    specials_total: int
    #: What is in the user's list.
    held: int
    held_specials: int
    #: The air date of the last regular episode in the list (ISO), for the airing estimate.
    last_aired: str | None = None
    #: Which regular episodes are in the list (item 36b). Empty in a file saved before build 250: upload the
    #: export again.
    held_numbers: list[int] = field(default_factory=list)
    #: What the export's ``FILES`` section says (build 258). Empty in a file saved earlier - ``Stored.files_read`` says
    #: which. Regular episodes with a file on the disk and no watched file of any kind; specials only counted.
    unwatched: list[int] = field(default_factory=list)
    unwatched_specials: int = 0
    #: Episodes with a file whose My List state is "unknown" (0) - AniDB does not know where the file is.
    unknown_state: list[int] = field(default_factory=list)
    unknown_specials: int = 0
    #: Files on the disk per release group id. AniDB's own number: the export carries no group names, and id 0
    #: ("no group") is left out.
    groups: dict[str, int] = field(default_factory=dict)
    #: Regular episodes held twice on the disk by two different groups.
    doubled: list[int] = field(default_factory=list)


@dataclass
class Stored:
    uploaded_at: str
    filename: str
    entries: list[Entry] = field(default_factory=list)
    #: Which reader made the entries (see ``PARSER_VERSION``). The file is read again when it is older.
    version: int = 1

    @property
    def files_read(self) -> bool:
        """Whether the entries carry the ``FILES`` facts above (a reader from build 258 on)."""
        return self.version >= 2


#: What the reader takes out of the export. **Raise it whenever the reader learns something new**: an export kept
#: with an older number is read again from the archive on the next load, so a new feature never needs the person
#: to upload the file again (build 259). 1 = the episode lists (build 244-250), 2 = the FILES section (258).
PARSER_VERSION = 2


# ---------------------------------------------------------------- reading the archive


def type_key(value: object) -> str:
    """AniDB's type name ("TV Series", "Music Video") as one key."""
    text = re.sub(r"[^a-z]", "", str(value or "").lower())
    if text in ("tv", "tvseries"):
        return "tv"
    if text in ("tvspecial", "special"):
        return "tvspecial"
    if text in ("musicvideo",):
        return "musicvideo"
    return text if text in TYPES else "unknown"


def _day(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def _mylist_rows(text: str) -> list[dict[str, str]]:
    """``mylist.csv`` as dicts. Titles may hold ``|`` and ``"``, so this is not a CSV reader.

    The thirteen columns from Year on are fixed; whatever sits between the id and them is the titles.
    The file ends with a genre list after a blank line, which is not part of the table.
    """
    lines = text.splitlines()
    if not lines or not lines[0].startswith("AID|"):
        raise ExportError("mylist.csv is missing or does not start with the AID column")
    columns = lines[0].split("|")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line.strip() or line.startswith("Gener"):
            break
        parts = line.split("|")
        if len(parts) != len(columns):
            if len(parts) < len(columns):
                continue
            parts = [parts[0], "|".join(parts[1 : len(parts) - 13]), "", ""] + parts[-13:]
        rows.append(dict(zip(columns, parts)))
    return rows


def _episodes(text: str) -> tuple[int, int, str | None, list[int]]:
    """(regular held, specials held, air date of the last regular one held, their numbers) from an anime's file.

    Read from the right: an episode name may contain ``|``, but the last columns are fixed.
    """
    held = specials = 0
    last: date | None = None
    numbers: list[int] = []
    for line in text.split("\nFILES")[0].splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 12 or parts[-1].strip() != "1":
            continue
        if parts[2].isdigit():
            held += 1
            numbers.append(int(parts[2]))
            aired = _day(parts[-5])
            if aired is not None and (last is None or aired > last):
                last = aired
        else:
            specials += 1
    return held, specials, last.isoformat() if last else None, sorted(set(numbers))


#: My List states of a file (AniDB's): 0 unknown, 1 on the disk, 2 on a CD/DVD, 3 deleted, 4 remote.
STATE_UNKNOWN, STATE_DISK = "0", "1"


@dataclass
class FileFacts:
    unwatched: list[int] = field(default_factory=list)
    unwatched_specials: int = 0
    unknown_state: list[int] = field(default_factory=list)
    unknown_specials: int = 0
    groups: dict[str, int] = field(default_factory=dict)
    doubled: list[int] = field(default_factory=list)


def _file_facts(text: str) -> FileFacts:
    """What an anime file's ``FILES`` section says about watching, state and release groups (build 258).

    Read by column *name*, so a column AniDB adds does not shift the ones used. An episode's number comes from the
    episode lines above (``EID`` -> ``EPNo``); one that is not a plain number (``S1``, ``C2``) is a special and is
    only counted. An episode is watched when **any** of its files is, whatever state that file is in.
    """
    head, sep, tail = text.partition("\nFILES")
    if not sep:
        return FileFacts()
    numbers: dict[str, int | None] = {}
    for line in head.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) >= 12:
            numbers[parts[1].strip()] = int(parts[2]) if parts[2].strip().isdigit() else None
    rows = [line for line in tail.splitlines() if line.strip()]
    if not rows:
        return FileFacts()
    columns = [name.strip() for name in rows[0].split("|")]
    try:
        at = {name: columns.index(name) for name in ("EID", "GID", "isWatched", "myState")}
    except ValueError:
        return FileFacts()

    files: dict[str, list[tuple[str, bool, str]]] = {}
    for line in rows[1:]:
        parts = line.split("|")
        if len(parts) != len(columns):
            continue
        files.setdefault(parts[at["EID"]].strip(), []).append(
            (parts[at["GID"]].strip(), parts[at["isWatched"]].strip() == "1", parts[at["myState"]].strip())
        )

    facts = FileFacts()
    groups: dict[str, int] = {}
    for eid, items in files.items():
        number = numbers.get(eid)
        on_disk = [item for item in items if item[2] == STATE_DISK]
        if on_disk and not any(watched for _gid, watched, _state in items):
            if number is None:
                facts.unwatched_specials += 1
            else:
                facts.unwatched.append(number)
        if any(item[2] == STATE_UNKNOWN for item in items):
            if number is None:
                facts.unknown_specials += 1
            else:
                facts.unknown_state.append(number)
        real = [gid for gid, _watched, _state in on_disk if gid not in ("", "0")]
        for gid in real:
            groups[gid] = groups.get(gid, 0) + 1
        if number is not None and len(set(real)) > 1:
            facts.doubled.append(number)
    facts.unwatched.sort()
    facts.unknown_state.sort()
    facts.doubled.sort()
    facts.groups = dict(sorted(groups.items(), key=lambda pair: (-pair[1], pair[0])))
    return facts


def parse(archive: bytes) -> list[Entry]:
    """Every anime in the export. Raises :class:`ExportError` when it is not one."""
    if len(archive) > MAX_UPLOAD_BYTES:
        raise ExportError("The file is too large to be a My List export")
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ExportError(f"This is not a .tgz archive ({exc})") from exc
    with tar:
        # One pass: a gzip stream cannot be seeked cheaply, so every file is read as it goes by.
        files: dict[str, str] = {}
        total = 0
        for index, member in enumerate(tar):
            if index >= MAX_MEMBERS:
                raise ExportError("The archive holds too many files to be a My List export")
            if not member.isfile():
                continue
            total += member.size
            if member.size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                raise ExportError("The archive is larger than a My List export can be")
            name = member.name.lstrip("./")
            if name != "mylist.csv" and not re.fullmatch(r"anime/a\d+\.csv", name):
                continue
            handle = tar.extractfile(member)
            if handle is not None:
                files[name] = handle.read().decode("utf-8", "replace")
        if "mylist.csv" not in files:
            raise ExportError("There is no mylist.csv in this archive - it is not an AniDB My List export")

        def read(name: str) -> str | None:
            return files.get(name)

        entries: list[Entry] = []
        for row in _mylist_rows(read("mylist.csv") or ""):
            if not row.get("AID", "").isdigit():
                continue
            aid = int(row["AID"])
            held = specials = 0
            last = None
            numbers: list[int] = []
            episodes = read(f"anime/a{aid}.csv")
            facts = FileFacts()
            if episodes:
                held, specials, last, numbers = _episodes(episodes)
                facts = _file_facts(episodes)
            entries.append(
                Entry(
                    aid=aid,
                    title=row.get("Romaji") or row.get("English") or f"AniDB {aid}",
                    english=(row.get("English") or "").strip() or None,
                    year=(row.get("Year") or "").strip(),
                    type=type_key(row.get("TypeName")),
                    adult=(row.get("isAdult") or "0").strip() == "1",
                    total=int(row["Eps"]) if (row.get("Eps") or "").isdigit() else 0,
                    specials_total=int(row["EpsSpec"]) if (row.get("EpsSpec") or "").isdigit() else 0,
                    held=held,
                    held_specials=specials,
                    last_aired=last,
                    held_numbers=numbers,
                    **asdict(facts),
                )
            )
    if not entries:
        raise ExportError("The export holds no anime")
    return entries


# ---------------------------------------------------------------- keeping it


def store_path() -> Path:
    settings = get_settings()
    return Path(str(settings.database_url).split("///")[-1]).parent / "anidb-mylist.json"


def archive_path() -> Path:
    """The uploaded file itself, kept beside the parsed list so a better reader can run over it again."""
    return store_path().with_name("anidb-export.tgz")


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def save(
    entries: list[Entry],
    filename: str,
    *,
    now: datetime | None = None,
    archive: bytes | None = None,
    uploaded_at: str | None = None,
) -> Stored:
    """Keep the parsed list, and - when the upload itself is handed in - the file it came from (build 259).

    The archive is the person's own export (their whole list), kept in the data folder with everything else that is
    theirs and never sent anywhere. Without it a new feature that reads more of the file needs a second upload,
    and they may have deleted the original by then.
    """
    stored = Stored(
        uploaded_at=uploaded_at or (now or datetime.now(UTC)).isoformat(),
        filename=filename[:200],
        entries=entries,
        version=PARSER_VERSION,
    )
    payload = {
        "uploaded_at": stored.uploaded_at,
        "filename": stored.filename,
        "version": PARSER_VERSION,
        "entries": [asdict(e) for e in entries],
    }
    with _lock:
        if archive is not None:
            _write_atomically(archive_path(), archive)
        _write_atomically(store_path(), json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return stored


def _upgraded(stored: Stored) -> Stored:
    """``stored`` read again from the kept archive by the current reader; unchanged when that cannot be done.

    A file saved before the archive was kept (or one that cannot be read any more) has nothing to read again, and
    stays as it is: the page then says to upload it once more.
    """
    path = archive_path()
    if not path.exists():
        return stored
    try:
        entries = parse(path.read_bytes())
    except (OSError, ExportError):
        return stored
    return save(entries, stored.filename, uploaded_at=stored.uploaded_at)


def load() -> Stored | None:
    """The last upload, or None when there is none (or it cannot be read - it is only a cache of a file).

    An upload read by an older reader is read again here from the kept archive, once, so what the reader learns
    later reaches a list that was uploaded before.
    """
    path = store_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        stored = Stored(
            uploaded_at=str(raw["uploaded_at"]),
            filename=str(raw.get("filename") or ""),
            entries=[Entry(**item) for item in raw["entries"]],
            # Build 258 wrote ``files_read: true`` and no version.
            version=int(raw.get("version") or (2 if raw.get("files_read") else 1)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if stored.version < PARSER_VERSION:
        stored = _upgraded(stored)
    return stored


# ---------------------------------------------------------------- filtering


@dataclass(frozen=True)
class Filters:
    """AniDB's filter box; the defaults are the set the reference list was made with."""

    adult: str = "hide"
    airing: str = "finished"
    status: str = "incomplete"
    #: Empty means every type.
    types: tuple[str, ...] = ()

    def cleaned(self) -> "Filters":
        d = Filters()
        return Filters(
            adult=self.adult if self.adult in ADULT else d.adult,
            airing=self.airing if self.airing in AIRING else d.airing,
            status=self.status if self.status in STATUS else d.status,
            types=tuple(kind for kind in self.types if kind in TYPES),
        )


def airing_state(entry: Entry, today: date) -> str:
    """finished | airing | unaired - an estimate, see the module note."""
    years = [int(year) for year in re.findall(r"\d{4}", entry.year)]
    if years and max(years) < today.year:
        return "finished"
    if years and min(years) > today.year:
        return "unaired"
    if entry.last_aired:
        remaining = max(0, entry.total - entry.held)
        projected = date.fromisoformat(entry.last_aired) + timedelta(days=7 * remaining)
        return "finished" if projected <= today else "airing"
    return "unaired"


#: The questions the AniDB page's other tabs ask of the export's file facts (build 258).
PROBLEMS = ("unwatched", "unknown", "groups")


def problem_count(entry: Entry, kind: str, *, only_doubled: bool = True) -> int:
    """How many things are wrong with this anime under ``kind``; 0 means it does not belong in that tab.

    * ``unwatched`` - episodes on the disk that no one has watched;
    * ``unknown`` - episodes with a file whose My List state is unknown;
    * ``groups`` - episodes held twice by two groups (``only_doubled``), or, without it, the groups themselves
      when there are two or more.
    """
    if kind == "unwatched":
        return len(entry.unwatched) + entry.unwatched_specials
    if kind == "unknown":
        return len(entry.unknown_state) + entry.unknown_specials
    if kind == "groups":
        if only_doubled:
            return len(entry.doubled)
        return len(entry.groups) if len(entry.groups) > 1 else 0
    raise ValueError(f"unknown problem kind: {kind}")


def select_problems(
    entries: list[Entry], kind: str, *, adult: str = "hide", only_doubled: bool = True
) -> list[Entry]:
    """The entries with something under ``kind``, by title like AniDB's list; adult as in :class:`Filters`."""
    if adult not in ADULT:
        adult = "hide"
    found = []
    for entry in entries:
        if adult == "hide" and entry.adult:
            continue
        if adult == "only" and not entry.adult:
            continue
        if problem_count(entry, kind, only_doubled=only_doubled) > 0:
            found.append(entry)
    found.sort(key=lambda item: item.title.lower())
    return found


def is_incomplete(entry: Entry) -> bool:
    return (entry.held > 0 or entry.held_specials > 0) and entry.held < entry.total


def select(entries: list[Entry], filters: Filters, *, today: date | None = None) -> list[Entry]:
    """The entries that pass ``filters``, by title like AniDB's list."""
    today = today or date.today()
    filters = filters.cleaned()
    found = []
    for entry in entries:
        if filters.types and entry.type not in filters.types:
            continue
        if filters.adult == "hide" and entry.adult:
            continue
        if filters.adult == "only" and not entry.adult:
            continue
        if filters.airing != "ignore" and airing_state(entry, today) != filters.airing:
            continue
        # "Complete" and "incomplete" both need something in the list: an anime that is only on the
        # account (nothing held at all) is neither.
        in_list = entry.held > 0 or entry.held_specials > 0
        if filters.status == "incomplete" and not is_incomplete(entry):
            continue
        if filters.status == "complete" and not (in_list and entry.held >= entry.total):
            continue
        found.append(entry)
    found.sort(key=lambda item: item.title.lower())
    return found

"""A record of release names the parser read badly (build 98).

Asked for: *"it feels like there should be somewhere an output, saving anime
title/filenames which are broken into a file so we can improve the parser over
time with this kind of anime."*

Which is the right instinct, and the reason is in this build's own history:
every parser fix so far came from a screenshot. The names that defeat it are
real, they are rare, and they are invisible unless somebody happens to be
looking at the one dialog that shows them. A file of them turns "I noticed
something odd last week" into a list to work through.

**What counts as badly read.** Not "unusual" - a name AniFlow handled
correctly is of no interest however strange it looks. Only the four ways a
parse is *unusable*:

* no title at all, so the release can belong to no anime;
* no episode and no sign of being a batch, so it can belong to no episode;
* an episode number sitting inside the title ("Show 4th Season - 21"), which
  is what makes every episode its own row;
* no subgroup where the name plainly credits one.

**What it is not.** Not a log, not telemetry, and never automatic sending.
It is a file in the configuration folder, it holds release names and nothing
about the person running AniFlow, and it is capped so an indexer having a bad
day cannot fill a disk. Each name is recorded once.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from backend.app.core.logging import for_component
from backend.app.services.release_parser import ParsedRelease

logger = for_component("Parser")

#: Where the file lives. Beside the databases rather than in the config
#: folder proper: the config folder is the source of truth for things the user
#: edits, and nobody edits this.
FILE_NAME = "unparsed-names.jsonl"

#: At most this many names. A tracker having a bad day must not fill a disk,
#: and a thousand examples is already more than anybody will work through.
MAX_ENTRIES = 1000

#: One writer at a time. The RSS scan, the queue pass and a dialog's search
#: can all reach this at once.
_LOCK = Lock()

#: An episode number left inside the title: "… 4th Season - 21", "Show - 03".
#: The reported case, and the one with a visible consequence - each episode
#: becomes its own row in the setup dialog.
#:
#: The dash is required. A title that simply *ends* in a number is a title
#: that ends in a number - "Gundam 00", "86", "Mushoku Tensei 2" - and
#: flagging those buries the real findings under correct parses.
_EPISODE_IN_TITLE = re.compile(
    r"[\s._]*[-–]\s*(?:ep?\s*)?\d{1,4}\s*$", re.IGNORECASE
)

#: A name that credits a group the way scene releases do, so "no subgroup" is
#: a miss rather than the truth.
_LOOKS_CREDITED = re.compile(r"^\[[^\]]+\]|\s-\s*[A-Za-z][A-Za-z0-9_.]*\s*(?:\([^()]*\))?$")

#: Shapes where having no episode number is correct rather than a failure.
#:
#: Movies were the first thing the record got wrong, on the day it shipped:
#: six of the deployment's first twenty-eight entries were films, which have
#: no episode and are not supposed to. A record full of things that are
#: working buries the ones that are not.
_NO_EPISODE_IS_FINE = (
    "batch", "complete", "season", "collection", "pack", "bd", "vol",
    "movie", "film", "gekijouban", "gekijōban", "ova", "oad", "special",
)


@dataclass(frozen=True)
class Finding:
    """One thing wrong with one parse."""

    kind: str
    raw_name: str
    detail: str


def findings_for(parsed: ParsedRelease) -> list[Finding]:
    """What is unusable about this parse, if anything.

    Pure, so the rules can be tested without touching a disk - and so the
    same judgement can be shown in the interface later without a file read.
    """
    raw = parsed.raw_name or ""
    found: list[Finding] = []
    if not (parsed.title or "").strip():
        found.append(Finding("no-title", raw, "nothing usable as a title"))
    elif _EPISODE_IN_TITLE.search(parsed.title or ""):
        found.append(
            Finding("episode-in-title", raw, f"title reads {parsed.title!r}")
        )
    if not parsed.episode and not any(
        word in raw.lower() for word in _NO_EPISODE_IS_FINE
    ):
        found.append(Finding("no-episode", raw, "no episode and no sign of a batch"))
    if not parsed.subgroup and _LOOKS_CREDITED.search(raw.strip()):
        found.append(Finding("no-subgroup", raw, "the name credits a group"))
    return found


def _path() -> Path:
    from backend.app.core.config import get_settings

    settings = get_settings()
    root = Path(str(settings.database_url).split("///")[-1]).parent
    return root / FILE_NAME


def record(parsed: ParsedRelease) -> list[Finding]:
    """Note a badly-read name, once, and return what was wrong with it.

    Best effort in every direction: a full disk, a read-only mount or a
    corrupt line must not interfere with parsing a release. The return value
    is the findings whether or not anything could be written.
    """
    found = findings_for(parsed)
    if not found:
        return []
    try:
        _append(found)
    except Exception as exc:  # noqa: BLE001 - a note is never worth an error
        # Deliberately everything. A full disk raises OSError, a path the
        # platform refuses raises ValueError, and neither is a reason to fail
        # the search that was being run.
        logger.debug("could not record an unparsed name: %s", exc)
    return found


def _append(found: list[Finding]) -> None:
    path = _path()
    with _LOCK:
        seen = {entry.get("raw_name") for entry in _read(path)}
        fresh = [finding for finding in found if finding.raw_name not in seen]
        if not fresh:
            return
        if len(seen) >= MAX_ENTRIES:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stamped = datetime.now(UTC).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            for finding in fresh:
                handle.write(
                    json.dumps(
                        {
                            "at": stamped,
                            "kind": finding.kind,
                            "raw_name": finding.raw_name,
                            "detail": finding.detail,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        logger.info(
            "recorded %d release name(s) the parser read badly in %s",
            len(fresh),
            path.name,
        )


def _read(path: Path) -> list[dict]:
    """What has been recorded so far. A bad line is skipped, not fatal."""
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def entries() -> list[dict]:
    """Everything recorded, for a page or a look at the file."""
    try:
        return _read(_path())
    except OSError:
        return []


def summary() -> dict[str, int]:
    """How many of each kind have been seen. What a report would show."""
    counts: dict[str, int] = {}
    for entry in entries():
        kind = str(entry.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


__all__ = [
    "FILE_NAME",
    "MAX_ENTRIES",
    "Finding",
    "entries",
    "findings_for",
    "record",
    "summary",
]

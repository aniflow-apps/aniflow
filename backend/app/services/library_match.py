"""Find an anime's files in the library, and say what they are.

For the mode that has no indexer. A basic entry is a watchlist row with a name
and a count, and the files are very often already on disk - put there by hand,
by an older tool, or by the user's own sorting. Asked for as:

    *"Then how we are going to implement a way how in basic mode it can detect
    you files in your media folder? ... it looks into the target folder and
    tries to detect if it has folder named similar to the anime and tries to
    link them, if it does not find anything, maybe it gives the option to the
    user to manually link a folder ... if the folder is found, then it tries to
    understand the file, maybe it can read the quality, subgroup etc and try to
    fill out the rest of the detail screen."*

Almost none of this is new machinery. ``importer._adopt_new_files`` already
walks an anime's folder, reads episode numbers out of filenames and records
each file against its episode; ``release_parser`` reads subgroup and quality;
``media_probe`` reads the true resolution off the file. What was missing is
the part nothing can do by itself: **which folder is this anime**, when its
name on disk is not the name AniFlow knows it by.

So this module does three things and writes nothing until it is told to:

* :func:`candidates` scores every folder in the library against every name the
  anime has - including the AniDB dump's aliases, which is what that dump is
  for - and returns them best first;
* :func:`preview` says what each file in a chosen folder would become,
  *including the ones it cannot place*;
* :func:`link` writes it: the folder, the files, and what the files say about
  subgroup and quality.

**Nothing above the known episode count is created.** That rule is the
importer's and it is kept here for the reason its comment gives - *"creating
rows from a filename on a timer is how one bad name becomes fifty phantom
episodes"* - and because a count that disagrees with the folder usually means
something the user should look at rather than something AniFlow should decide.
Those files are reported, in the preview and afterwards in the activity log,
the same way Shoko-mode adoption reports them.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, Episode, EpisodeStatus, EpisodeType
from backend.app.services.episode_state import record_media_file, refresh_episode_status
from backend.app.services.library import episode_numbers_in
from backend.app.services.release_parser import parse_release_name
from backend.app.services.storage import StorageRoots, is_media_file, storage_roots, roots_for

logger = for_component("Library")


def _key(text: str) -> str:
    """A name reduced to what identifies it, in any script.

    Not ``release_parser.normalize_title_for_match``, which keeps ``[a-z0-9]``
    only: a folder named 葬送のフリーレン reduces to nothing there and scores zero
    against the very alias that exists to match it. That helper is right for
    comparing release names against provider titles and is left alone; this
    one has to survive a library somebody named in Japanese.
    """
    lowered = text.casefold().replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", lowered, flags=re.UNICODE)).strip()

#: How many folders are offered without asking for the full list.
TOP_CANDIDATES = 5


@dataclass
class FolderCandidate:
    """One folder in the library, and how much it looks like this anime."""

    name: str
    #: 0-100. 100 is an exact match on one of the anime's names.
    score: int
    #: Which of the anime's names it matched, so the score is explainable.
    matched: str | None
    #: Media files directly in it or one level down.
    files: int
    #: Distinct episode numbers those files parse to.
    episodes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "score": self.score,
            "matched": self.matched,
            "files": self.files,
            "episodes": self.episodes,
        }


@dataclass
class FilePlan:
    """What one file in the chosen folder would become."""

    filename: str
    episode: int | None
    #: False when AniFlow will not touch it, with ``reason`` saying why.
    placed: bool
    reason: str | None = None
    quality: str | None = None
    subgroup: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "episode": self.episode,
            "placed": self.placed,
            "reason": self.reason,
            "quality": self.quality,
            "subgroup": self.subgroup,
        }


@dataclass
class MatchPreview:
    """Everything the dialog needs to show before anything is written."""

    folder: str
    plans: list[FilePlan] = field(default_factory=list)
    #: What the files agree on, where they agree. Written on link.
    subgroup: str | None = None
    quality: str | None = None
    release_name: str | None = None

    @property
    def placeable(self) -> int:
        return sum(1 for plan in self.plans if plan.placed)

    @property
    def unplaceable(self) -> int:
        return sum(1 for plan in self.plans if not plan.placed)

    def as_dict(self) -> dict[str, object]:
        return {
            "folder": self.folder,
            "plans": [plan.as_dict() for plan in self.plans],
            "placeable": self.placeable,
            "unplaceable": self.unplaceable,
            "subgroup": self.subgroup,
            "quality": self.quality,
            "release_name": self.release_name,
        }


def names_of(anime: Anime) -> list[str]:
    """Every name this anime goes by, for matching a folder against.

    The AniDB aliases are in here deliberately: a folder named in romaji for a
    show AniFlow knows by its English title is the ordinary case, and that
    dump is the one thing that knows both.
    """
    names = [
        anime.display_name,
        anime.local_name,
        anime.title_english,
        anime.title_romaji,
        anime.title_native,
        *(anime.anidb_titles or {}).values(),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        text = (name or "").strip()
        key = _key(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _score(folder: str, names: list[str]) -> tuple[int, str | None]:
    """How much a folder name looks like one of these names, and which one.

    Deliberately simple and explainable, because the number is shown to a
    person who has to decide whether to trust it:

    * 100 - the same name, ignoring punctuation and case
    * 85  - one contains the other ("Frieren" in "Sousou no Frieren (2023)")
    * 40-80 - that share of the words in common
    """
    haystack = _key(folder)
    if not haystack:
        return 0, None
    best = 0
    winner: str | None = None
    for name in names:
        needle = _key(name)
        if not needle:
            continue
        if needle == haystack:
            return 100, name
        if needle in haystack or haystack in needle:
            score = 85
        else:
            words = set(needle.split())
            found = set(haystack.split())
            shared = words & found
            if not shared:
                continue
            score = int(40 + 40 * (len(shared) / max(len(words), len(found))))
        if score > best:
            best, winner = score, name
    return best, winner


def _media_files(folder: Path) -> list[Path]:
    """The video files in a folder, and one level inside it.

    One level because a season folder is normal and two levels of nesting is
    somebody else's library layout, not this anime's episodes.
    """
    found: list[Path] = []
    try:
        for entry in sorted(folder.iterdir(), key=lambda path: path.name):
            if entry.is_file() and is_media_file(entry):
                found.append(entry)
            elif entry.is_dir():
                try:
                    found.extend(
                        child
                        for child in sorted(entry.iterdir(), key=lambda path: path.name)
                        if child.is_file() and is_media_file(child)
                    )
                except OSError:
                    continue
    except OSError:
        return []
    return found


def candidates(
    anime: Anime,
    roots: StorageRoots | None = None,
    *,
    all_folders: bool = False,
) -> list[FolderCandidate]:
    """The library's folders, best match first.

    Everything is returned when ``all_folders`` is set, which is what "show me
    the rest" in the dialog asks for: a folder named nothing like the show is
    exactly the case the user has to solve by hand.
    """
    roots = roots_for(anime, roots or storage_roots())
    names = names_of(anime)
    library = roots.media_root
    try:
        folders = [entry for entry in library.iterdir() if entry.is_dir()]
    except OSError as exc:
        logger.info("the library folder could not be read: %s", exc)
        return []

    scored: list[FolderCandidate] = []
    for folder in folders:
        score, matched = _score(folder.name, names)
        files = _media_files(folder)
        numbers = set(episode_numbers_in([path.name for path in files]))
        numbers.discard(None)
        scored.append(
            FolderCandidate(
                name=folder.name,
                score=score,
                matched=matched,
                files=len(files),
                episodes=len(numbers),
            )
        )
    scored.sort(key=lambda item: (-item.score, item.name.lower()))
    if all_folders:
        return scored
    # A folder nothing matched is still offered when little else is: an empty
    # list would read as "your library is empty", which is a different thing.
    return [item for item in scored if item.score > 0][:TOP_CANDIDATES] or scored[:TOP_CANDIDATES]


def preview(anime: Anime, folder_name: str, roots: StorageRoots | None = None) -> MatchPreview:
    """What linking this folder would do. Writes nothing.

    The unplaceable files are the point of showing this at all: a folder with
    twelve episodes and three extras is normal, and so is a folder that turns
    out to be the wrong show.
    """
    roots = roots_for(anime, roots or storage_roots())
    folder = (roots.media_root / folder_name).resolve()
    library = roots.media_root.resolve()
    if library not in folder.parents and folder != library:
        # The name comes from a browser, so it is input: a folder outside the
        # library is not this anime's, whatever it is called.
        raise ValueError("That folder is not in the library")

    known = anime.effective_episode_count or anime.episode_count or 0
    result = MatchPreview(folder=folder_name)
    subgroups: Counter[str] = Counter()
    qualities: Counter[str] = Counter()
    titles: Counter[str] = Counter()

    files = _media_files(folder)
    numbered = episode_numbers_in([path.name for path in files])
    for path, number in zip(files, numbered):
        shown = path.relative_to(folder).as_posix()
        parsed = None
        try:
            parsed = parse_release_name(path.name)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.subgroup:
                subgroups[parsed.subgroup] += 1
            if parsed.resolution:
                qualities[parsed.resolution] += 1
            if parsed.title:
                titles[parsed.title] += 1
        if number is None:
            result.plans.append(
                FilePlan(
                    filename=shown,
                    episode=None,
                    placed=False,
                    reason="no_episode_number",
                    quality=parsed.resolution if parsed else None,
                    subgroup=parsed.subgroup if parsed else None,
                )
            )
            continue
        if known and number > known:
            # The importer's rule, kept: reported, never acted on.
            result.plans.append(
                FilePlan(
                    filename=shown,
                    episode=number,
                    placed=False,
                    reason="above_episode_count",
                    quality=parsed.resolution if parsed else None,
                    subgroup=parsed.subgroup if parsed else None,
                )
            )
            continue
        result.plans.append(
            FilePlan(
                filename=shown,
                episode=number,
                placed=True,
                quality=parsed.resolution if parsed else None,
                subgroup=parsed.subgroup if parsed else None,
            )
        )

    # What the files agree on. Only stored when they agree with themselves -
    # a folder holding two groups' copies says nothing about a preference.
    result.subgroup = _agreed(subgroups)
    result.quality = _agreed(qualities)
    result.release_name = _agreed(titles)
    return result


def _agreed(counts: Counter[str]) -> str | None:
    """The one value the files agree on, or None when they do not.

    "Agree" is a clear majority rather than unanimity: one oddly named file in
    a folder of twelve should not throw the answer away.
    """
    if not counts:
        return None
    value, hits = counts.most_common(1)[0]
    return value if hits * 2 > sum(counts.values()) else None


def link(
    session: Session,
    anime: Anime,
    folder_name: str,
    roots: StorageRoots | None = None,
) -> dict[str, object]:
    """Record the folder and its files against this anime.

    Everything it learns is written to the fields that own it, whatever mode
    the entry is in - *"if a field requires a value it should get only this
    value and not something with is mode depend"*. So a basic entry comes out
    of this with a media folder, its episodes placed, and a profile carrying
    the subgroup and quality its own files are in; converting it later then
    has everything except the one answer a human has to give.
    """
    from backend.app.services.attention import log_activity

    roots = roots_for(anime, roots or storage_roots())
    plan = preview(anime, folder_name, roots)
    folder = roots.media_root / folder_name

    episodes = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
    }
    known = anime.effective_episode_count or anime.episode_count or 0

    anime.media_folder = folder_name
    placed = 0
    for entry in plan.plans:
        if not entry.placed or entry.episode is None:
            continue
        episode = episodes.get(entry.episode)
        if episode is None:
            # Within the known count but with no row - an entry added before
            # rows were created for its mode. Made rather than skipped: the
            # count says this episode exists.
            episode = Episode(
                anime_id=anime.id,
                number=entry.episode,
                episode_type=EpisodeType.REGULAR,
                status=EpisodeStatus.UNAVAILABLE,
            )
            session.add(episode)
            episodes[entry.episode] = episode
        path = folder / entry.filename
        record_media_file(
            episode,
            filename=entry.filename,
            size_bytes=_size_of(path),
            version=1,
        )
        refresh_episode_status(episode, roots=roots, check_disk=True)
        placed += 1

    learned = _remember(session, anime, plan)
    session.commit()

    # The files nothing could be done with, reported the way the import pass
    # reports them - the user pressed a button, so the answer belongs in the
    # log as well as in the dialog.
    unplaced = [entry for entry in plan.plans if not entry.placed]
    if unplaced:
        above = sorted({entry.episode for entry in unplaced if entry.episode is not None})
        nameless = sum(1 for entry in unplaced if entry.episode is None)
        parts = []
        if above:
            parts.append(
                f"episode(s) {', '.join(str(number) for number in above)} are above the"
                f" known episode count ({known})"
            )
        if nameless:
            parts.append(f"{nameless} file(s) have no episode number in the name")
        log_activity(
            session,
            kind="LIBRARY_UNPLACED",
            message=f"{anime.display_name}: " + "; ".join(parts) + " - left alone",
            details={"folder": folder_name, "files": [entry.filename for entry in unplaced]},
            anime_id=anime.id,
        )
        session.commit()

    logger.info(
        "linked %s to %s: %s file(s) placed, %s left alone",
        anime.display_name, folder_name, placed, len(unplaced),
    )
    return {
        "folder": folder_name,
        "placed": placed,
        "unplaced": len(unplaced),
        "learned": learned,
    }


def _remember(session: Session, anime: Anime, plan: MatchPreview) -> dict[str, str | None]:
    """Store what the files said about themselves.

    A release profile rather than nothing, even with no indexer to use it:
    *"the more infos we have and we can add them to the db we should"*, and
    this is the difference between a conversion that asks one question and one
    that asks four.
    """
    from backend.app.models.release_profile import ReleaseProfile

    if plan.release_name and not anime.release_name:
        anime.release_name = plan.release_name
    if not (plan.subgroup or plan.quality):
        return {"subgroup": None, "resolution": None, "release_name": anime.release_name}

    profile = anime.profile
    if profile is None:
        profile = ReleaseProfile(anime_id=anime.id)
        session.add(profile)
        session.flush()
        anime.profile = profile
    if plan.subgroup and not profile.subgroups:
        profile.subgroups = [plan.subgroup]
    if plan.quality and not profile.resolution:
        profile.resolution = plan.quality
    return {
        "subgroup": plan.subgroup,
        "resolution": plan.quality,
        "release_name": anime.release_name,
    }


def _size_of(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def unlink(session: Session, anime: Anime) -> None:
    """Forget the folder and the files, leaving both on disk.

    The way out of a wrong answer. It clears what AniFlow recorded and touches
    nothing in the library: the same contract as clearing a download.
    """
    anime.media_folder = None
    for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id)):
        if episode.media_filename:
            episode.media_filename = None
            episode.media_size_bytes = None
            episode.media_imported_at = None
            episode.media_version = None
            refresh_episode_status(episode)
    session.commit()
    logger.info("%s: the library folder and its files were unlinked", anime.display_name)


__all__ = [
    "FilePlan",
    "FolderCandidate",
    "MatchPreview",
    "TOP_CANDIDATES",
    "candidates",
    "link",
    "names_of",
    "preview",
    "unlink",
]

"""The three storage stages and every path AniFlow builds from them.

AniFlow moves an episode through three roots:

``download_root``
    Where the torrent client writes. There is no per-anime folder here: the
    client drops files straight into the root, so an episode's source path is
    simply ``download_root / episode.download_filename``.

``import_root``
    The hand-over folder in Shoko Server mode: AniFlow puts finished downloads
    here and Shoko takes them from it. Unset in standalone mode - nothing
    mounts the volume - so every import path resolves to ``None`` and callers
    must treat the stage as unavailable.

``media_root``
    The canonical library. Files live one folder deep, under the anime's
    stored ``media_folder``.

Every path in AniFlow is built here so that the "where would this go?"
question has exactly one answer. Folder names are *stored data*, never
re-derived from the anime title at use time: a later naming stage may decide
an anime titled "Saijo no Osewa" belongs in "Saijo no Osewa S1", and the
answer must not change underneath files already on disk.

All builders return ``None`` rather than guessing when a required piece of
data is missing, and every result is validated to stay inside its root.
"""
import os
from dataclasses import dataclass, replace
from pathlib import Path

from backend.app.core.config import get_settings

#: Media containers AniFlow recognises as an episode's video file.
MEDIA_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".ogm"}

#: Explicitly *not* an episode's media file, even when sitting next to it.
SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}


def is_media_file(path: Path | str) -> bool:
    """True when the name looks like a video container (never a subtitle)."""
    suffix = Path(path).suffix.lower()
    return suffix in MEDIA_SUFFIXES


@dataclass(frozen=True)
class StorageRoots:
    """The resolved roots for one operation."""

    download_root: Path
    media_root: Path
    import_root: Path | None = None
    #: True when a caller named the media root explicitly (a test, a debug
    #: override). A pinned root is the answer for every anime: the library an
    #: anime belongs to must not quietly override what was asked for.
    media_root_pinned: bool = False

    def as_dict(self) -> dict[str, str | None]:
        return {
            "download_root": str(self.download_root),
            "import_root": str(self.import_root) if self.import_root else None,
            "media_root": str(self.media_root),
        }


def storage_roots(
    *,
    download_root: Path | str | None = None,
    media_root: Path | str | None = None,
    import_root: Path | str | None = None,
) -> StorageRoots:
    """Resolve the three roots, letting callers (and tests) override any of them."""
    env = get_settings()
    resolved_import = import_root if import_root is not None else env.import_root
    return StorageRoots(
        download_root=Path(download_root) if download_root is not None else Path(env.download_root),
        media_root=Path(media_root) if media_root is not None else Path(env.anime_library_root),
        import_root=Path(resolved_import) if resolved_import else None,
        media_root_pinned=media_root is not None,
    )


def roots_for(anime, roots: StorageRoots | None = None) -> StorageRoots:
    """The roots for one anime: its library's folder is its media root.

    Build 192 (0.8.5). Every anime belongs to a library, and a library is a
    folder, so "where does this anime's media live?" has one answer *per
    anime* instead of one for the installation. Everything that builds a
    media path from an anime goes through here; the builders below still
    take plain roots.

    An anime with no library yet (or one whose library has no folder) keeps
    the installation's media root, and a media root pinned by the caller
    always wins.
    """
    base = roots if roots is not None else storage_roots()
    if base.media_root_pinned or anime is None:
        return base
    folder = getattr(getattr(anime, "library", None), "folder", None)
    if not folder:
        return base
    return replace(base, media_root=Path(folder))


def within_root(root: Path, candidate: Path) -> bool:
    """Whether ``candidate`` stays inside ``root`` (symlinks resolved)."""
    return safe_relative(root, candidate) is not None


def safe_relative(root: Path, candidate: Path) -> Path | None:
    """``candidate`` relative to ``root``, or None when it escapes the root."""
    try:
        return candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        pass
    # Container mounts can make resolve() disagree (the path may not exist on
    # this side of the mount), so fall back to a textual comparison. The
    # candidate MUST be normalised first: without collapsing "..", a path like
    # <root>/../../etc/passwd still starts with <root> and would be accepted.
    try:
        root_parts = Path(os.path.normpath(Path(root).absolute())).parts
        candidate_parts = Path(os.path.normpath(Path(candidate).absolute())).parts
    except (ValueError, OSError):
        return None
    if candidate_parts[: len(root_parts)] != root_parts:
        return None
    return Path(*candidate_parts[len(root_parts):]) if len(candidate_parts) > len(root_parts) else Path()


def _one_level(root: Path, folder: str | None, filename: str | None) -> Path | None:
    """``root / folder / filename`` with both parts constrained to one level."""
    if not filename:
        return None
    target = root / folder / filename if folder else root / filename
    return target if within_root(root, target) else None


def download_path(
    roots: StorageRoots,
    download_filename: str | None,
    download_folder: str | None = None,
) -> Path | None:
    """Where the torrent client put this episode's file, if the name is known.

    ``download_folder`` is the client's category subfolder when it uses one
    (``<download_root>/AniFlow/…``); NULL means directly in the root.
    """
    return _one_level(roots.download_root, download_folder, download_filename)


def download_path_candidates(
    roots: StorageRoots,
    download_filename: str | None,
    *,
    download_folder: str | None = None,
    category: str | None = None,
) -> list[Path]:
    """Every place this episode's downloaded file could reasonably be.

    Deployments differ in whether AniFlow and the torrent client see the same
    absolute path, so the layout is *probed* rather than assumed: the stored
    folder first, then the download root itself, then the configured category
    subfolder. Callers take the first one that exists.
    """
    if not download_filename:
        return []
    seen: list[Path] = []
    for folder in (download_folder, None, category):
        candidate = _one_level(roots.download_root, folder, download_filename)
        if candidate is not None and candidate not in seen:
            seen.append(candidate)
    return seen


#: The three places a file can be, in the order they are looked in.
STAGES = ("library", "import", "download")


@dataclass(frozen=True)
class FileLocation:
    """Where an episode's file is, and nothing else.

    ``stage`` is one of :data:`STAGES`, or None when the file is in none of
    them - which is a problem to report, never a reason to download again.
    """

    stage: str | None = None
    path: Path | None = None

    @property
    def found(self) -> bool:
        return self.stage is not None

    def __bool__(self) -> bool:  # so `if located:` reads as intended
        return self.found


def locate_episode_file(
    roots: StorageRoots,
    episode,
    *,
    category: str | None = None,
) -> FileLocation:
    """Where this episode's file is: library, import folder, download folder.

    Looked for **from the end of the pipeline backwards** (build 136), which is
    the rule this was built to:

        *"it should first check is the file in the target ... if not, check is
        the file in the import folder ... if not, check is the file in the
        download folder ... if file is not available in all 3 folders, then its
        a problem. It should not go from the front, because the folder could be
        somewhere else."*

    The order carries the meaning. The library is where a finished episode
    belongs, so finding it there ends the question, and looking there first
    means nothing earlier in the pipeline can be mistaken for the answer. An
    episode that is in the library *and* still has a leftover download is in
    the library.

    Before this, every caller answered "do I already have this?" from the
    database instead - ``media_version`` in one place, ``media_filename`` in
    another, a live download row in a third - and an episode whose recorded
    version happened to be NULL was downloaded again, repeatedly, while
    sitting in the library.

    What this is not:

    * **Not a search.** It looks for the names AniFlow recorded, so it answers
      "is what I wrote down still there". Finding files nobody wrote down is
      ``reconcile_media``'s job.
    * **Not permission to download.** ``FileLocation()`` - stage None - means
      nobody can find the file, and an unmounted volume looks exactly like a
      deleted one. Callers use this to *refuse*, never to proceed.
    """
    anime = getattr(episode, "anime", None)
    roots = roots_for(anime, roots)

    library = media_path(
        roots, getattr(anime, "media_folder", None), episode.media_filename
    )
    if library is not None and library.exists():
        return FileLocation("library", library)

    handed_over = import_path(roots, getattr(anime, "import_folder", None), episode.import_filename)
    if handed_over is not None and handed_over.exists():
        return FileLocation("import", handed_over)

    for candidate in download_path_candidates(
        roots,
        episode.download_filename,
        download_folder=episode.download_folder,
        category=category,
    ):
        if candidate.exists():
            return FileLocation("download", candidate)

    return FileLocation()


def media_folder_path(roots: StorageRoots, media_folder: str | None) -> Path | None:
    """The anime's folder inside the media root."""
    if not media_folder:
        return None
    target = roots.media_root / media_folder
    return target if within_root(roots.media_root, target) else None


def media_path(roots: StorageRoots, media_folder: str | None, media_filename: str | None) -> Path | None:
    """The canonical location of one episode's media file."""
    if not media_folder:
        return None
    return _one_level(roots.media_root, media_folder, media_filename)


def import_path(roots: StorageRoots, import_folder: str | None, import_filename: str | None) -> Path | None:
    """Where a file waits for Shoko Server. ``None`` with no import root."""
    if roots.import_root is None or not import_folder:
        return None
    return _one_level(roots.import_root, import_folder, import_filename)


def pick_media_file(folder: Path) -> tuple[Path | None, str | None]:
    """The single video file inside ``folder``.

    Returns ``(path, None)`` when exactly one media file is present, and
    ``(None, reason)`` otherwise - a pack with several videos is ambiguous and
    must not be guessed at. Subtitles are ignored entirely.
    """
    try:
        candidates = sorted(
            entry for entry in folder.rglob("*") if entry.is_file() and is_media_file(entry)
        )
    except OSError:
        return None, "source_unreadable"
    if not candidates:
        return None, "no_media_found"
    if len(candidates) > 1:
        return None, "ambiguous_multi_video"
    return candidates[0], None


def host_path(container_path: Path | str) -> str | None:
    """The host folder behind a container mount, if it can be worked out.

    Reads ``/proc/self/mountinfo``, where each line records the source path a
    mount came from. Returns None off Linux, when the path is not a mount of
    its own (so nothing was bound there), or when the file cannot be read -
    all of which mean "cannot say", never a guess.
    """
    target = str(container_path).rstrip("/") or "/"
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        # 0:id 1:parent 2:major:minor 3:root 4:mount-point 5:options ...
        fields = line.split()
        if len(fields) < 5 or fields[4] != target:
            continue
        source = fields[3]
        if not source or source == "/":
            # A whole filesystem rather than a folder inside one: the source
            # says nothing the mount point does not.
            return None
        return source
    return None


def path_status(container_path: Path | str | None) -> dict[str, object]:
    """One storage root, as the Settings page needs to show it."""
    if container_path is None:
        return {"container": None, "host": None, "exists": False, "writable": False}
    path = Path(container_path)
    exists = path.is_dir()
    return {
        "container": str(path),
        "host": host_path(path),
        "exists": exists,
        "writable": exists and os.access(path, os.W_OK),
    }


__all__ = [
    "STAGES",
    "FileLocation",
    "locate_episode_file",
    "MEDIA_SUFFIXES",
    "SUBTITLE_SUFFIXES",
    "StorageRoots",
    "download_path",
    "download_path_candidates",
    "host_path",
    "import_path",
    "is_media_file",
    "media_folder_path",
    "media_path",
    "path_status",
    "pick_media_file",
    "safe_relative",
    "storage_roots",
    "within_root",
]

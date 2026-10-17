"""What this copy of AniFlow is: a version, and a build number.

Two numbers, because they answer two different questions.

``VERSION`` is semantic and describes *what the software is*: ``1.x.x`` a
version, ``x.1.x`` a release, ``x.x.1`` a hotfix. It says nothing about how
many times the code changed to get there, and it deliberately stands still
across a dozen fixes that are not worth a number of their own.

``BUILD`` only ever grows, by one, whenever anything ships. That is the number
you can actually check against a running container: two copies claiming
``0.1.0`` can be different code, two copies claiming build 68 cannot. The whole
point is to tell "my change is not working" apart from "my change is not
there", which is otherwise a very expensive afternoon.

``BUILD.md`` carries the same two numbers plus the date as one filename-safe
string, for naming the backup copy of a build so the archive says what it holds.
"""
from datetime import date
from functools import lru_cache
from pathlib import Path

#: The repository root, where the markers live next to ``backend/``.
_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def app_version() -> str:
    """The version declared in the VERSION file next to ``backend/``."""
    return _read("VERSION") or "0.0.0"


@lru_cache(maxsize=1)
def build_number() -> int:
    """The build declared in the BUILD file, or 0 if it is missing.

    Zero rather than a guess: a build number that is wrong is worse than one
    that is obviously absent, because the entire use of it is being trusted.
    """
    raw = _read("BUILD")
    try:
        return int(raw)
    except ValueError:
        return 0


def version_label() -> str:
    """What the interface shows: ``v.0.1.0 b.68``."""
    return f"v.{app_version()} b.{build_number()}"


def build_slug(when: date | None = None) -> str:
    """``AniFlow_b68_v0_1_0_d26_09_06`` - one build, as a filename.

    Dots become underscores because this is meant to be pasted into a folder
    name, and the date is year-month-day so a directory listing sorts by it.
    """
    stamp = when or date.today()
    version = app_version().replace(".", "_")
    return f"AniFlow_b{build_number()}_v{version}_d{stamp:%y_%m_%d}"


@lru_cache(maxsize=1)
def build_string() -> str:
    """The slug this build was stamped with, from ``BUILD.md``.

    Read rather than recomputed, so it keeps the date the build was made
    instead of quietly becoming today's date every time it is asked for.
    """
    for line in _read("BUILD.md").splitlines():
        candidate = line.strip().strip("`")
        if candidate.startswith("AniFlow_b"):
            return candidate
    return build_slug()


@lru_cache(maxsize=1)
def user_agent() -> str:
    """How AniFlow identifies itself to everything it asks.

    An honest name and the real version - not a browser string. Public APIs
    and trackers are entitled to know who is asking, several of them throttle
    or refuse clients that will not say, and a self-hosted tool pretending to
    be Firefox would be lying to the people running the sites it depends on.
    """
    return f"AniFlow/{app_version()}"


def _read(name: str) -> str:
    try:
        return (_ROOT / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""

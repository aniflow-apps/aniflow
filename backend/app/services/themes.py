"""Themes: what the interface looks like, from a folder (build 90).

A theme is a folder with one file in it:

    config/themes/my-theme/theme.yaml

and that file says what it is called, who wrote it, which version of it this
is, what it looks like - and, importantly, which *theme format* it was written
for. AniFlow's built-in themes live in ``backend/app/themes/`` and are
discovered the same way, so copying one is how you start writing your own.

They sit inside the package rather than at the top of the repository for a
deployment reason: the deploy script mirrors ``backend/`` and ``frontend/``
and copies a named handful of root files, so a new top-level folder is
invisible to it. Build 90 first shipped them as ``themes/`` and the image
would not build - ``COPY themes/`` had nothing to copy.

**Why a list of colours and not a stylesheet.** The interface is already built
out of about thirty CSS custom properties - ``--bg``, ``--text``,
``--accent-strong`` and so on - so a theme has nothing to say that is not one
of those values. Declaring them means every value can be *checked* before it
reaches a browser: each one has to look like a colour, so a theme cannot
smuggle in ``url(http://somewhere/beacon.png)``, cannot close the rule and
add its own, and cannot hide a control by giving it ``display: none``. A
theme that turns out to be badly written makes AniFlow look wrong, which it
can always be told to stop doing; a theme that can run arbitrary CSS is a
different kind of thing to download from a stranger.

Every token is optional. What a theme leaves out keeps the built-in value, so
a theme that only changes the accent colour is four lines long.

**The format version.** ``themes_api: 1`` is what a theme states, and
:data:`THEMES_API` is what this build understands. They are compared exactly.
When the vocabulary changes in a way that would make an old theme wrong -
tokens renamed, meanings changed - this number goes up, and the themes
written for the old one are *listed and refused* rather than half-applied.
Refusing is the point: a theme applied against a vocabulary it does not know
produces a screen that is subtly wrong in ways nobody can debug from the
outside.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from backend.app.core.logging import for_component

logger = for_component("Themes")

#: The theme format this build understands. A theme states the same number.
#: See the module docstring for what a bump means.
THEMES_API = 1

#: The theme AniFlow falls back to: the one the stylesheet itself declares.
#: Never refused, never missing - if the folder is gone the built-in values
#: are still in the bundle, so "no theme" and "the default theme" look alike.
DEFAULT_SLUG = "aniflow-dark"

#: Colour tokens, named exactly as the CSS custom properties are (minus the
#: leading dashes) so a themer can read frontend/src/styles.css and know what
#: to write. Anything not listed here is rejected as a typo, which is what
#: makes "serface: #fff" a visible mistake rather than a silent no-op.
COLOR_TOKENS = (
    "bg", "bg-deep",
    "surface", "surface-2", "surface-3", "surface-raised",
    "field",
    "border", "border-strong",
    "text", "text-dim", "text-mute",
    "accent", "accent-strong", "accent-soft", "accent-ink", "accent-nav",
    "success", "success-border", "success-bg",
    "warning", "warning-border",
    "danger", "danger-border", "danger-bg",
)

#: The three corner radii. Separate from the colours because they are checked
#: differently - a length, not a colour.
RADIUS_TOKENS = ("lg", "md", "sm")

#: Whether the browser should paint its *own* widgets light or dark: dropdown
#: popups, scrollbars, focus rings, date pickers (build 92). None of those are
#: AniFlow's markup, so no colour token reaches them - this is the one word
#: that does.
#:
#: A theme that omits it keeps the bundled value, which is dark. Optional and
#: additive, so every theme written for format 1 stays valid.
SCHEMES = ("dark", "light")

#: What a colour may look like. Hex with three, four, six or eight digits, or
#: one of the four CSS colour functions with nothing but numbers, percentages,
#: commas, slashes and spaces inside. Deliberately narrow: the value goes into
#: a stylesheet, and everything a colour needs is in here.
_COLOR = re.compile(
    r"^(?:#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9a-zA-Z.%,/\s+-]{1,60}\)"
    r"|transparent)$"
)

#: What a radius may look like: a number, optionally with a CSS length unit.
_RADIUS = re.compile(r"^(?:0|[0-9]{1,3}(?:\.[0-9]{1,3})?(?:px|rem|em|%)?)$")

#: How long a name, author, version or description may be. Truncated rather
#: than refused - an over-long description is untidy, not dangerous.
_TEXT_LIMIT = 300


@dataclass
class Theme:
    """One theme folder, read and checked."""

    slug: str
    name: str
    author: str = ""
    version: str = ""
    description: str = ""
    #: The format the file says it is written for.
    themes_api: int = 0
    #: "dark" or "light": what the browser paints its own widgets with.
    color_scheme: str = ""
    #: True for the themes that ship with AniFlow.
    builtin: bool = False
    colors: dict[str, str] = field(default_factory=dict)
    radius: dict[str, str] = field(default_factory=dict)
    #: Why this theme cannot be used, if it cannot. A theme with a problem is
    #: still listed - a theme that vanishes from the list because of a typo
    #: is a theme nobody can fix.
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.problems


def builtin_dir() -> Path:
    """Where the themes that ship with AniFlow live.

    Inside the package, so they travel with it: the deploy script mirrors
    ``backend/`` wholesale and the Dockerfile copies it wholesale, which
    means these need no line of their own in either.
    """
    # services/ -> app/
    return Path(__file__).resolve().parents[1] / "themes"


def user_dir() -> Path:
    """Where a user's own themes live, beside the settings file."""
    from backend.app.services.config_file import config_path

    return config_path().parent / "themes"


def _text(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip()[:_TEXT_LIMIT]


def _read(folder: Path, *, builtin: bool) -> Theme | None:
    """One folder as a Theme, or None if there is no theme file in it."""
    path = folder / "theme.yaml"
    if not path.is_file():
        return None
    slug = folder.name
    theme = Theme(slug=slug, name=slug, builtin=builtin)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        theme.problems.append(f"theme.yaml could not be read: {exc}")
        return theme
    if not isinstance(raw, dict):
        theme.problems.append("theme.yaml does not describe a theme")
        return theme

    theme.name = _text(raw.get("name"), slug) or slug
    theme.author = _text(raw.get("author"))
    theme.version = _text(raw.get("version"))
    theme.description = _text(raw.get("description"))

    declared = raw.get("themes_api")
    try:
        theme.themes_api = int(declared)
    except (TypeError, ValueError):
        theme.problems.append(
            "themes_api is missing. Add `themes_api: "
            f"{THEMES_API}` - it says which theme format this file is written for."
        )
        return theme
    if theme.themes_api != THEMES_API:
        theme.problems.append(
            f"written for theme format v{theme.themes_api};"
            f" this build of AniFlow uses v{THEMES_API}"
        )
        return theme

    theme.colors = _checked(raw.get("colors"), COLOR_TOKENS, _COLOR, "colors", theme)
    theme.radius = _checked(raw.get("radius"), RADIUS_TOKENS, _RADIUS, "radius", theme)

    scheme = _text(raw.get("color_scheme")).lower()
    if scheme and scheme not in SCHEMES:
        theme.problems.append(
            f"color_scheme: {scheme!r} is not one of {', '.join(SCHEMES)}"
        )
    else:
        theme.color_scheme = scheme
    return theme


def _checked(
    section: object,
    allowed: tuple[str, ...],
    pattern: re.Pattern[str],
    label: str,
    theme: Theme,
) -> dict[str, str]:
    """One section of the file, keeping only what is a known token and valid.

    A bad value is dropped and reported rather than making the whole theme
    unusable: the rest of it still applies, and the Settings page can say
    which line to look at.
    """
    if section is None:
        return {}
    if not isinstance(section, dict):
        theme.problems.append(f"{label} is not a list of values")
        return {}
    kept: dict[str, str] = {}
    for key, value in section.items():
        token = str(key).strip()
        if token not in allowed:
            theme.problems.append(f"{label}: {token!r} is not a token AniFlow knows")
            continue
        text = str(value).strip()
        if not pattern.match(text):
            theme.problems.append(f"{label}.{token}: {text!r} is not a valid value")
            continue
        kept[token] = text
    return kept


def discover() -> list[Theme]:
    """Every theme AniFlow can see, built-in and user, by name.

    A user theme with the same folder name as a built-in one replaces it, so
    a built-in can be adjusted by copying it into the config folder and
    editing it there - which is also what stops an update overwriting the
    edit.
    """
    found: dict[str, Theme] = {}
    for folder, builtin in ((builtin_dir(), True), (user_dir(), False)):
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            continue
        for entry in entries:
            theme = _read(entry, builtin=builtin)
            if theme is not None:
                found[theme.slug] = theme
    return sorted(found.values(), key=lambda item: (not item.builtin, item.name.casefold()))


def get(slug: str) -> Theme | None:
    """One theme by folder name, or None."""
    for theme in discover():
        if theme.slug == slug:
            return theme
    return None


def stylesheet(theme: Theme, *, credits: bool = True) -> str:
    """The theme as CSS: one rule, only the tokens it declares.

    Nothing else is emitted, so a theme cannot affect layout, hide a control
    or load anything over the network. What it leaves out keeps the value the
    bundled stylesheet declares.

    ``credits`` writes the theme's name, author and version into a comment at
    the top, which is what somebody reading their own theme's output wants.
    It is turned off for the one endpoint that answers without a session: a
    list of colours says nothing about anybody, and a name and an author do.
    """
    lines = []
    if credits:
        lines += [
            f"/* {theme.name}",
            f" * {theme.description}" if theme.description else " *",
            f" * {theme.author or 'unknown author'}"
            f"{f', version {theme.version}' if theme.version else ''}"
            f" - theme format v{theme.themes_api}",
            " */",
        ]
    lines.append(":root {")
    for token in COLOR_TOKENS:
        if token in theme.colors:
            lines.append(f"  --{token}: {theme.colors[token]};")
    for token in RADIUS_TOKENS:
        if token in theme.radius:
            lines.append(f"  --radius-{token}: {theme.radius[token]};")
    if theme.color_scheme:
        # Not a custom property: the browser reads this one itself.
        lines.append(f"  color-scheme: {theme.color_scheme};")
    lines.append("}")
    return "\n".join(lines) + "\n"


__all__ = [
    "COLOR_TOKENS",
    "SCHEMES",
    "DEFAULT_SLUG",
    "RADIUS_TOKENS",
    "THEMES_API",
    "Theme",
    "builtin_dir",
    "discover",
    "get",
    "stylesheet",
    "user_dir",
]

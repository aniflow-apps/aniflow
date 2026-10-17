"""The rule collection.

Importing this package registers the default pipeline.  The import order below
does **not** matter -- execution order comes from each rule's ``order``
attribute -- but it is listed in pipeline order so the file reads like a table
of contents.

Pipeline order and the reasoning behind it:

===== ============================ =================================================
Order Rule                         Why it runs here
===== ============================ =================================================
100   ``crc32``                    8 hex digits in their own bracket: unmistakable.
110   ``season-episode``           ``S01E09`` fixes season *and* episode at once.
120   ``season``                   Explicit season/part/cour words.
130   ``episode``                  Episode keywords, ranges, lone numbers.
140   ``quality``                  ``1080p``-shaped tokens are never anything else.
150   ``video``                    Codec/bit-depth tokens.
160   ``audio``                    Codec/channel/track-type tokens.
170   ``subtitles``                Needs audio done first (``Dual`` disambiguation).
180   ``release``                  Source/service, once codecs cannot be confused.
185   ``curly-annotation``         Whole-brace notes, after real metadata is taken.
190   ``flags``                    Uncensored/batch/repack/media type/extras.
200   ``year``                     A bracketed year, once brackets are mostly empty.
210   ``title-tail``               Ambiguous trailing number: needs everything above.
220   ``absolute-episode``         Needs the per-season episode to exist first.
230   ``subgroup``                 A bracket nobody else wanted.
240   ``title``                    Text nobody else wanted.
245   ``alternative-titles``       Secondary titles, after the primary one is fixed.
250   ``only-name``                Derived from ``name``.
900   ``misc``                     Everything still unclaimed.
905   ``tags``                     Flatten recognised fields into one list.
910   ``finalize``                 Cross-field consistency and tidy-up.
===== ============================ =================================================

To add a rule, drop a module in this package, decorate the class with
``@register`` and import it below.  Nothing else needs to change.
"""

from __future__ import annotations

from .base import Rule, Stage, clear_registry, register, registered_rules

# Imported for their registration side effects, in pipeline order.
from . import crc as _crc                      # noqa: F401  (order 100)
from . import season_episode as _season_episode  # noqa: F401  (order 110)
from . import season as _season                # noqa: F401  (order 120)
from . import episode as _episode              # noqa: F401  (order 130)
from . import quality as _quality              # noqa: F401  (order 140)
from . import video as _video                  # noqa: F401  (order 150)
from . import audio as _audio                  # noqa: F401  (order 160)
from . import subtitles as _subtitles          # noqa: F401  (order 170)
from . import release as _release              # noqa: F401  (order 180)
from . import flags as _flags                  # noqa: F401  (order 185, 190)
from . import year as _year                    # noqa: F401  (order 200)
from . import title_tail as _title_tail        # noqa: F401  (order 210)
from . import absolute as _absolute            # noqa: F401  (order 220)
from . import subgroup as _subgroup            # noqa: F401  (order 230)
from . import title as _title                  # noqa: F401  (order 240, 245, 250)
from . import misc as _misc                    # noqa: F401  (order 900)
from . import tags as _tags                    # noqa: F401  (order 905)
from . import finalize as _finalize            # noqa: F401  (order 910)

from .extension import extract_extension

__all__ = [
    "Rule",
    "Stage",
    "register",
    "registered_rules",
    "clear_registry",
    "default_rules",
    "extract_extension",
]


def default_rules() -> list[Rule]:
    """Every registered rule, instantiated and ordered."""
    return registered_rules()

"""Conservative, extensible parser for messy anime/media filenames.

Quick start::

    from anime_filename_parser import parse

    result = parse("[SubsPlease] Jujutsu Kaisen 2 (1080p) [B4D12414].mkv")
    result.name        # 'Jujutsu Kaisen 2'
    result.only_name   # 'Jujutsu Kaisen'
    result.episode     # 2
    result.quality     # '1080p'
    result.crc32       # 'B4D12414'
    result.tags        # ['1080p']  -- every recognised tag, canonically spelled
    result.to_dict()   # JSON-ready mapping

The input does not have to be a filename: a release title, a folder name or any
bare string parses the same way, with ``extension`` left ``None``.

Design rules the library holds itself to:

* **Conservative** -- when a value cannot be determined confidently it stays
  ``None`` and a warning explains why.  Nothing is guessed.
* **Lossless** -- text that is recognised but has no dedicated field goes to
  ``misc``; text that is not recognised at all goes to ``misc`` *and*
  ``unknown_tokens`` with its location.  The original filename is never modified.
* **Explainable** -- ``warnings`` describes every ambiguous decision and
  ``provenance`` records which rule set which field.
* **Offline** -- no anime database, no network, no external service. Deterministic.
* **Extensible** -- parsing is a staged pipeline of small, independently
  ordered rules; new release formats are new rules, not new special cases.

The CLI (``python -m anime_filename_parser``) is just another consumer of this
same engine and is never needed to use the library.
"""

from __future__ import annotations

from .context import ParseContext, Roles
from .models import MediaType, ParseResult, UnknownToken
from .normalization import NormalizationConfig, NormalizedText, normalize
from .pipeline import Parser, default_parser, parse, parse_many
from .rules import Rule, Stage, default_rules, register
from .tokenizer import Enclosure, Group, Token, TokenKind, tokenize

__version__ = "0.2.3"

__all__ = [
    "__version__",
    # primary API
    "parse",
    "parse_many",
    "Parser",
    "default_parser",
    "ParseResult",
    "UnknownToken",
    "MediaType",
    # extension points
    "Rule",
    "Stage",
    "register",
    "default_rules",
    "ParseContext",
    "Roles",
    # lower-level building blocks
    "NormalizationConfig",
    "NormalizedText",
    "normalize",
    "tokenize",
    "Token",
    "TokenKind",
    "Group",
    "Enclosure",
]

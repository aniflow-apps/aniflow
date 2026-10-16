"""Taking secrets out of what leaves AniFlow as a file (build 262).

The diagnostics bundle is made to be sent to someone else, so this is the one place that decides what must not go
with it: passwords, keys and tokens - and nothing else. Anime titles, folder paths and addresses stay, because a
report without them cannot be read; the bundle's README and the button beside it say so.

Three layers, because each catches what the others cannot:

1. **The values themselves.** Every secret AniFlow holds (the torrent client's password, the proxy's, the Shoko key,
   a client id) is replaced wherever it appears, in any text, whatever it is next to. This is what catches a secret
   inside a URL, an error message or a traceback that no pattern was written for.
2. **Names that look like a secret** - a ``password=`` in a query string, a ``"api_key": "…"`` in JSON, an
   ``Authorization:`` header, a ``Bearer`` token, the user and password in ``http://user:pass@host``.
3. **Settings by key name** - a settings object is walked and any field called like a secret is blanked.

``test_diagnostics.py`` makes a bundle out of an installation full of known secrets and fails on any of them
appearing anywhere in it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

MASK = "***"

#: A secret shorter than this is not replaced by value: "1234" would turn every 1234 in the log into ***, and the
#: pattern layers still apply to it where it sits next to its name.
MIN_SECRET_LENGTH = 6

#: Words that make a field or parameter name a secret. Matched as a whole *segment* of the name (split on ``_``,
#: ``.`` and ``-``), never as a part of a word: ``qb_password`` and ``shoko_api_key`` are secrets, ``design`` and
#: ``compass`` are not. The joined spellings (``apiKey``, ``authkey``) are listed whole.
_SECRET_WORDS = ("password", "passwd", "pwd", "passkey", "pass", "token", "secret", "key", "sig", "signature",
                 "pair", "session", "sid", "cookie", "authorization", "auth", "credential", "credentials",
                 "apikey", "authkey", "rsskey", "accesstoken", "refreshtoken", "clientsecret", "torrentpass")

_NAME = r"(?:[A-Za-z0-9]+[_.\-])*(?:" + "|".join(_SECRET_WORDS) + r")(?:[_.\-][A-Za-z0-9]+)*"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # http://user:pass@host -> http://***@host
    (re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\g<scheme>" + MASK + "@"),
    # ?name=value and &name=value, where the name says it is a secret
    (re.compile(r"(?P<lead>[?&;])(?P<name>" + _NAME + r")=(?P<value>[^&\s\"'<>]*)", re.I), r"\g<lead>\g<name>=" + MASK),
    # name=value outside a query string (key=value logging, config lines)
    (re.compile(r"(?P<name>\b" + _NAME + r")\s*=\s*(?P<value>[^\s,;&\"'<>]+)", re.I), r"\g<name>=" + MASK),
    # "name": "value" in JSON
    (re.compile(r"(?P<head>\"" + _NAME + r"\"\s*:\s*\")[^\"]*(?P<tail>\")", re.I), r"\g<head>" + MASK + r"\g<tail>"),
    # 'name': 'value' in a Python dict that was printed
    (re.compile(r"(?P<head>'" + _NAME + r"'\s*:\s*')[^']*(?P<tail>')", re.I), r"\g<head>" + MASK + r"\g<tail>"),
    # Authorization: Basic ... / Cookie: ... / Set-Cookie: ... headers, to the end of the line
    (re.compile(r"(?P<name>\b(?:Authorization|Proxy-Authorization|Cookie|Set-Cookie|X-Api-Key|X-Auth-Token))\s*:\s*[^\r\n]+", re.I),
     r"\g<name>: " + MASK),
    # Bearer tokens anywhere
    (re.compile(r"\b(?P<scheme>Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{8,}", re.I), r"\g<scheme> " + MASK),
    # a tracker's passkey in the path: .../announce/<32 hex>
    (re.compile(r"(?P<lead>/announce/)[0-9A-Fa-f]{16,}", re.I), r"\g<lead>" + MASK),
    # the one-time code for the account
    (re.compile(r"(?P<lead>one-time code[^:\r\n]*:\s*)\S+", re.I), r"\g<lead>" + MASK),
)

_SECRET_NAME = re.compile(r"^" + _NAME + r"$", re.I)
#: ``apiKey`` -> ``api_key`` before the name is read.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
#: Settings that only *report* a secret ("has_qb_password": true) are not secrets themselves.
_REPORTS = re.compile(r"^has_", re.I)


def is_secret_name(name: str) -> bool:
    """Whether a field called ``name`` holds a secret (``qb_password``, ``shoko_api_key``, ``token``)."""
    return not _REPORTS.match(name) and bool(_SECRET_NAME.match(_CAMEL.sub("_", name)))


def secret_values(values: Iterable[object]) -> list[str]:
    """The strings worth replacing by value: long enough, longest first (so one inside another is not left behind)."""
    unique = {str(value) for value in values if isinstance(value, str) and len(value) >= MIN_SECRET_LENGTH}
    return sorted(unique, key=len, reverse=True)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` with every given secret value and every secret-looking pattern masked."""
    for secret in secrets:
        text = text.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any, secrets: Iterable[str] = (), *, name: str = "") -> Any:
    """A settings value, walked: a secret-named field is masked (or reported as set / not set), the rest scrubbed."""
    kept = list(secrets)
    if isinstance(value, Mapping):
        return {key: redact_value(item, kept, name=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item, kept, name=name) for item in value]
    if name and is_secret_name(name):
        if value in (None, "", 0, False):
            return value  # nothing is stored, and saying so is useful
        return MASK
    if isinstance(value, str):
        return redact_text(value, kept)
    return value


__all__ = ["MASK", "is_secret_name", "redact_text", "redact_value", "secret_values"]

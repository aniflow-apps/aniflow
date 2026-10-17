"""Where AniFlow's own requests go out from (build 71).

Two kinds of outgoing request, and only one of them is anybody else's
business:

* **The public internet** - AniList for metadata, the indexer and RSS feeds
  for releases. These say what you are watching and what you are looking for,
  to services that log them. This is what a proxy is for, and it is what gets
  proxied.
* **Your own network** - qBittorrent and Shoko, on a LAN address. Sending
  those through a proxy would ask a machine on the other side of the world to
  reach 192.168.1.7, which cannot work. They stay direct, always.

**A proxy here does not hide your torrent traffic.** That traffic is
qBittorrent's, not AniFlow's - it never passes through this process, and no
setting here can touch it. qBittorrent has its own proxy settings for exactly
that, and they are the ones that matter for what a swarm can see. What this
does hide is which shows AniFlow asks about, and where it asks.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import quote, urlparse

from backend.app.core.logging import for_component

logger = for_component("Net")

#: What a proxy can be. ``socks5h`` resolves hostnames at the proxy, which is
#: the point of using one - ``socks5`` leaks the name to your own resolver.
KINDS = ("none", "http", "socks4", "socks5", "socks5h")


class ProxyError(RuntimeError):
    """The proxy is configured but unusable, said out loud rather than guessed."""


def proxy_url(settings=None) -> str | None:
    """The proxy every internet-facing request should use, or None.

    Built from the settings each time rather than cached: a proxy is changed
    exactly when something is wrong, and a cached value would keep sending
    traffic the old way while the page says otherwise.
    """
    from backend.app.services.app_settings import load_app_settings

    resolved = settings if settings is not None else load_app_settings()
    kind = (getattr(resolved, "proxy_kind", "none") or "none").strip().lower()
    host = (getattr(resolved, "proxy_host", "") or "").strip()
    port = getattr(resolved, "proxy_port", None)
    if kind == "none" or not host or not port:
        return None
    if kind not in KINDS:
        raise ProxyError(f"{kind} is not a proxy type AniFlow knows")

    user = (getattr(resolved, "proxy_username", "") or "").strip()
    secret = getattr(resolved, "proxy_password", "") or ""
    # Quoted: a password with an @ or a colon in it would otherwise rewrite the
    # URL and point the request somewhere else entirely.
    credentials = f"{quote(user, safe='')}:{quote(secret, safe='')}@" if user else ""
    return f"{kind}://{credentials}{host}:{port}"


def is_local(url: str) -> bool:
    """Whether a URL points into the deployment's own network.

    Local traffic must never be proxied. Checked by address rather than by
    which integration is calling, so a new caller cannot get this wrong by
    forgetting to ask.
    """
    host = urlparse(url).hostname or ""
    if not host or host in ("localhost", "host.docker.internal"):
        return True
    if host.endswith((".local", ".lan", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def client_kwargs(url: str | None = None, settings=None) -> dict[str, object]:
    """The httpx keyword arguments for one outgoing request.

    ``{}`` when no proxy applies, so every call site can pass this through
    unconditionally and none of them has to know the rules.
    """
    if url is not None and is_local(url):
        return {}
    try:
        proxy = proxy_url(settings)
    except ProxyError as exc:
        # A misconfigured proxy must not silently become a direct connection:
        # the whole point may have been not to make that request unproxied.
        raise
    return {"proxy": proxy} if proxy else {}


#: Free services that say which address the internet sees, tried in this order (build 233). One hard-wired service
#: answered 429 Too Many Requests - a rate limit says nothing about the proxy - so the check goes on to the next
#: one. The first two also name the country, the third only the address.
EXIT_PROVIDERS: tuple[str, ...] = (
    "https://api.country.is/",
    "https://ipwho.is/",
    "https://api.ipify.org?format=json",
)


def describe_exit(settings=None, *, timeout: float = 10.0) -> dict[str, object]:
    """Which address the internet sees, checked through the proxy in force.

    A proxy that is set but not working looks exactly like one that is working
    until something fails hours later, so this exists to be pressed. It reaches
    a third-party echo service - the only way to learn your own public address -
    and reports what it says, or why it could not be asked.

    The service from the settings is asked first, then the built-in ones: a
    service that is rate-limiting, or gone, must not read as a broken proxy. Only
    when every one of them fails is that the answer, with what each one said.
    """
    from urllib.parse import urlsplit

    import httpx

    from backend.app.core.version import user_agent
    from backend.app.services.app_settings import load_app_settings

    resolved = settings if settings is not None else load_app_settings()
    configured = (getattr(resolved, "proxy_check_url", "") or "").strip()
    endpoints = list(dict.fromkeys(item for item in (configured, *EXIT_PROVIDERS) if item))

    try:
        proxy = proxy_url(resolved)
    except ProxyError as exc:
        return {"ok": False, "detail": str(exc)}

    failures: list[str] = []
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent()},
        **({"proxy": proxy} if proxy else {}),
    ) as client:
        for endpoint in endpoints:
            name = urlsplit(endpoint).netloc or endpoint
            try:
                response = client.get(endpoint)
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPError as exc:
                failures.append(f"{name} could not be reached: {str(exc)[:80]}")
                continue
            except ValueError as exc:
                failures.append(f"{name} gave an unreadable answer: {str(exc)[:60]}")
                continue
            ip = _first(body, ("ip", "query", "ip_addr", "address"))
            if not ip:
                failures.append(f"{name} answered without an address")
                continue
            country = _first(body, ("country", "country_name", "countryCode", "country_code"))
            code = _first(body, ("country_code", "countryCode", "country"))
            return {
                "ok": True,
                "proxied": proxy is not None,
                "ip": ip,
                "country": country,
                "flag": _flag(code),
                "detail": None,
                # Which service answered, so a different country from one and the next is not a mystery.
                "via": name,
            }
    return {
        "ok": False,
        "proxied": proxy is not None,
        "detail": "no address-check service answered - " + "; ".join(failures),
    }


def _first(body: object, keys: tuple[str, ...]) -> str | None:
    """The first of several spellings a service might use for one field.

    Every IP-echo service names things differently and any of them may be
    swapped in, so nothing here depends on one provider's JSON.
    """
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _flag(code: str | None) -> str | None:
    """A two-letter country code as its flag, e.g. ``DE`` -> 🇩🇪.

    Built from regional indicator letters rather than an image, so it needs no
    asset, no lookup table and no request.
    """
    if not code or len(code) != 2 or not code.isalpha():
        return None
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code.upper())


__all__ = ["EXIT_PROVIDERS", "KINDS", "ProxyError", "client_kwargs", "describe_exit", "is_local", "proxy_url"]

"""Bring the database in line with the configuration folders.

Each of the three folders owns its table: a file is created, changed or
removed, and the corresponding row follows. Nothing here decides anything - it
copies what the files say - so the answer to "why is this indexer enabled?" is
always "because its file says so".

Run before listing any of the three, which is what makes a file dropped into
the folder show up in the UI without an import step or a restart.

Rows are matched to files by ``config_slug``, not by an id derived from the
file name, so renaming an object moves its file and keeps its id - and every
anime pointing at "the SubsPlease profile" keeps pointing at it.

Deletions are honoured: a row whose file is gone is removed. That is the point
of the folder being the truth, and it is why this only ever touches these
three tables - deleting an *anime* because a file vanished would be a very
different thing.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.indexer import Indexer
from backend.app.models.profile_template import ProfileTemplate
from backend.app.models.release_profile import CensorPreference
from backend.app.models.rss import FeedSource
from backend.app.services.managed_feeds import (
    FeedAlreadyConfigured,
    managed_feed,
    sync_managed_feed,
)

from backend.app.services.config_store import (
    FEEDS,
    INDEXERS,
    SUBGROUPS,
    ConfigFolder,
    as_bool,
    as_list,
    as_text,
    delete_object,
    read_folder,
    slugify,
    write_object,
)

logger = for_component("Config")


def _by_slug(session: Session, model, slug: str):
    """The row this file owns, if it already exists."""
    return session.scalar(select(model).where(model.config_slug == slug))


def _adopt(session: Session, model, slug: str, name: str):
    """Find or create the row for one file.

    An object created in the UI before this ran, or carried over from an older
    version, is adopted by name rather than duplicated - otherwise upgrading
    would silently double every profile.
    """
    row = _by_slug(session, model, slug)
    if row is not None:
        return row, False
    row = session.scalar(select(model).where(model.name == name))
    if row is not None:
        row.config_slug = slug
        return row, True
    row = model(config_slug=slug)
    session.add(row)
    return row, True


def _apply(row, values: dict[str, Any]) -> bool:
    changed = False
    for field, value in values.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    return changed


def _as_site_url(url: str | None, search_url: str | None, filename: str) -> str | None:
    """The site address, undoing one specific way it used to get overwritten.

    A URL with ``{query}`` in it is a perfectly good way to configure an
    indexer - paste the site's search URL and AniFlow fills the placeholders -
    so those are left alone.

    What is repaired is narrower: ``url`` holding *this file's own*
    ``search_url``, resolved against its own host. Only the Test button's old
    "suggestion" produced that, and the result half-worked in the worst way:
    searches still ran, but a URL with a query string means no file claims the
    site, so the indexer quietly stopped standing up its RSS source.
    """
    if not url or not search_url or "{base}" not in search_url:
        return url
    parts = urlsplit(url)
    if not (parts.scheme and parts.netloc):
        return url
    origin = f"{parts.scheme}://{parts.netloc}"
    if url.rstrip("/") != search_url.replace("{base}", origin).rstrip("/"):
        return url
    logger.warning(
        "indexer %s: url had been overwritten with this file's own search_url; "
        "using %s again so the file claims its site (and its feed) once more",
        filename, origin,
    )
    return origin


def _sync_indexers(session: Session) -> int:
    changed = 0
    seen: set[str] = set()
    for entry in read_folder(INDEXERS):
        data: dict[str, Any] = entry["data"]
        url = _as_site_url(
            as_text(data.get("url")), as_text(data.get("search_url")), entry["path"].name
        )
        if not url:
            logger.warning("indexer %s has no url", entry["path"].name)
            continue
        slug = entry["slug"]
        seen.add(slug)
        name = as_text(data.get("name")) or slug
        row, created = _adopt(session, Indexer, slug, name)
        changed += int(created)
        changed += int(_apply(row, {
            "name": name,
            "url": url,
            "enabled": as_bool(data.get("enabled"), True),
            "is_default": as_bool(data.get("default"), False),
        }))
        # A file that supplies a feed URL stands its RSS source up by saying
        # so; `use_as_rss: false` is how you keep the search and skip the feed.
        session.flush()
        wanted = as_bool(data.get("use_as_rss"), bool(as_text(data.get("rss_url"))))
        try:
            sync_managed_feed(row, session, wanted=wanted)
        except FeedAlreadyConfigured as exc:
            logger.warning(
                "indexer %s: %s is already configured as an RSS source, so no "
                "feed was stood up for it", entry["path"].name, exc,
            )
    changed += _forget_missing(session, Indexer, seen)
    return changed


def _sync_feed_sources(session: Session) -> int:
    changed = 0
    seen: set[str] = set()
    for entry in read_folder(FEEDS):
        data: dict[str, Any] = entry["data"]
        url = as_text(data.get("url"))
        if not url:
            logger.warning("RSS source %s has no url", entry["path"].name)
            continue
        slug = entry["slug"]
        seen.add(slug)
        name = as_text(data.get("name")) or slug
        clash = session.scalar(select(FeedSource).where(FeedSource.url == url))
        if clash is not None and clash.config_slug != slug:
            if clash.indexer_id is not None:
                # The indexer stood this feed up from its rss_url, an instant earlier on a database with no rows.
                # A file that names the address is the person saying so explicitly, and the address is unique, so
                # the file wins and the indexer's own copy goes (build 269: every startup failed on a fresh
                # database with the folders already full).
                logger.info("RSS source %s replaces the feed indexer %r stood up for %s", entry["path"].name, clash.name, url)
                session.delete(clash)
                session.flush()
                changed += 1
            elif clash.config_slug is None:
                # Made in the interface or by an older version: this file takes it over rather than duplicating it.
                clash.config_slug = slug
            else:
                logger.warning(
                    "RSS source %s names %s, which %s already owns; the second file is ignored",
                    entry["path"].name, url, clash.config_slug,
                )
                continue
        row, created = _adopt(session, FeedSource, slug, name)
        changed += int(created)
        changed += int(_apply(row, {
            "name": name,
            "url": url,
            "enabled": as_bool(data.get("enabled"), True),
            "is_default": as_bool(data.get("default"), False),
        }))
    # A feed an indexer stands up has no file of its own: it is derived from
    # the indexer's, and the foreign key removes it with its indexer.
    changed += _forget_missing(
        session, FeedSource, seen, keep=lambda row: row.indexer_id is not None
    )
    return changed


def _sync_templates(session: Session) -> int:
    changed = 0
    seen: set[str] = set()
    for entry in read_folder(SUBGROUPS):
        data: dict[str, Any] = entry["data"]
        slug = entry["slug"]
        seen.add(slug)
        name = as_text(data.get("name")) or slug
        row, created = _adopt(session, ProfileTemplate, slug, name)
        changed += int(created)
        changed += int(_apply(row, {
            "name": name,
            "subgroups": as_list(data.get("subgroups")),
            "resolution": as_text(data.get("resolution")),
            "media_source": as_text(data.get("media_source")),
            "edition": as_list(data.get("edition")),
            "edition_not": as_list(data.get("edition_not")),
            "censor_preference": _censor(data.get("censor_preference")),
            "require_title_match": as_bool(data.get("require_title_match"), True),
            "require_resolution_known": as_bool(data.get("require_resolution_known"), False),
            "require_source_known": as_bool(data.get("require_source_known"), False),
            "require_censor_known": as_bool(data.get("require_censor_known"), False),
            "is_default": as_bool(data.get("default"), False),
        }))
    changed += _forget_missing(session, ProfileTemplate, seen)
    return changed


def _censor(value: Any) -> CensorPreference:
    try:
        return CensorPreference(str(value or "ANY").strip().upper())
    except ValueError:
        return CensorPreference.ANY


def _forget_missing(session: Session, model, seen: set[str], keep=None) -> int:
    """Drop rows whose file is gone.

    Only rows that *have* a slug: one created in the UI in the same request
    has not been written out yet, and a row from an older version that nothing
    adopted is left alone rather than deleted out from under the user.
    """
    removed = 0
    for row in session.scalars(select(model).where(model.config_slug.is_not(None))):
        if row.config_slug in seen:
            continue
        if keep is not None and keep(row):
            continue
        session.delete(row)
        removed += 1
        logger.info("removed %s %r: its file is gone", model.__tablename__, row.name)
    return removed


def sync_from_files(session: Session) -> int:
    """Mirror all three folders into the database. Returns the change count."""
    changed = _sync_indexers(session) + _sync_feed_sources(session) + _sync_templates(session)
    if changed:
        session.commit()
    return changed


# --------------------------------------------------------------------------
# rows -> files
# --------------------------------------------------------------------------
def _store(folder: ConfigFolder, row, data: dict[str, Any]) -> None:
    """Write one row out, moving its file when the name changed."""
    slug = slugify(row.name)
    if row.config_slug and row.config_slug != slug:
        delete_object(folder, row.config_slug)
    row.config_slug = slug
    write_object(folder, slug, data)


def _indexer_payload(
    row: Indexer, search_url: str | None, rss_url: str | None, use_as_rss: bool = True
) -> dict[str, Any]:
    data: dict[str, Any] = {"name": row.name, "url": row.url}
    if search_url:
        data["search_url"] = search_url
    if rss_url:
        data["rss_url"] = rss_url
    data["enabled"] = row.enabled
    data["default"] = row.is_default
    if rss_url:
        data["use_as_rss"] = use_as_rss
    return data


def _feed_payload(row: FeedSource) -> dict[str, Any]:
    return {
        "name": row.name,
        "url": row.url,
        "enabled": row.enabled,
        "default": row.is_default,
    }


def _template_payload(row: ProfileTemplate) -> dict[str, Any]:
    return {
        "name": row.name,
        "subgroups": list(row.subgroups or []),
        "resolution": row.resolution,
        "media_source": row.media_source,
        "edition": list(row.edition or []),
        "edition_not": list(row.edition_not or []),
        "censor_preference": row.censor_preference.value,
        "require_title_match": row.require_title_match,
        "require_resolution_known": row.require_resolution_known,
        "require_source_known": row.require_source_known,
        "require_censor_known": row.require_censor_known,
        "default": row.is_default,
    }


def indexer_to_file(
    row: Indexer, *, search_url: str | None = None, rss_url: str | None = None
) -> None:
    """Write one indexer back to its file, so the folder stays the truth."""
    _store(INDEXERS, row, _indexer_payload(row, search_url, rss_url))


def feed_source_to_file(row: FeedSource) -> None:
    _store(FEEDS, row, _feed_payload(row))


def template_to_file(row: ProfileTemplate) -> None:
    _store(SUBGROUPS, row, _template_payload(row))


def _payload_for(session: Session, folder: ConfigFolder, row) -> dict[str, Any] | None:
    """What this row's file should contain, or None when it has no file."""
    if folder is SUBGROUPS:
        return _template_payload(row)
    if folder is FEEDS:
        # A managed feed is described by its indexer's file, not its own.
        return None if row.indexer_id else _feed_payload(row)
    if folder is INDEXERS:
        search_url, rss_url = file_mechanics(row)
        return _indexer_payload(
            row, search_url, rss_url, managed_feed(row.id, session) is not None
        )
    return None


def write_back(session: Session, folder: ConfigFolder, model) -> int:
    """Make every file of one kind agree with its row.

    Called after a change rather than for the one row that changed, because
    the flags that make one object the default clear it on all the others -
    and a file still claiming to be the default would win on the next read.
    """
    written = 0
    on_disk = {entry["slug"]: entry["data"] for entry in read_folder(folder)}
    for row in session.scalars(select(model)):
        payload = _payload_for(session, folder, row)
        if payload is None:
            continue
        slug = slugify(row.name)
        if row.config_slug and row.config_slug != slug:
            delete_object(folder, row.config_slug)
        elif on_disk.get(slug) == payload:
            row.config_slug = slug
            continue  # already says exactly this; leave the file alone
        row.config_slug = slug
        write_object(folder, slug, payload)
        written += 1
    return written


def forget_file(folder: ConfigFolder, row) -> None:
    """Remove the file behind a row the user deleted in the UI."""
    if row.config_slug:
        delete_object(folder, row.config_slug)


__all__ = [
    "FEEDS",
    "INDEXERS",
    "SUBGROUPS",
    "feed_source_to_file",
    "forget_file",
    "file_mechanics",
    "indexer_to_file",
    "sync_from_files",
    "write_back",
    "template_to_file",
]


def file_mechanics(row: Indexer) -> tuple[str | None, str | None]:
    """The search and feed templates already in this indexer's file.

    The UI cannot edit them - they are the site-specific part, and the point of
    the file is that a human wrote them - so a save from the UI has to carry
    them through unchanged rather than dropping them.
    """
    slug = row.config_slug or slugify(row.name)
    for entry in read_folder(INDEXERS):
        if entry["slug"] == slug:
            return as_text(entry["data"].get("search_url")), as_text(entry["data"].get("rss_url"))
    return None, None

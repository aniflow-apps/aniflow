"""The feed source an indexer owns.

When an indexer's file supplies the site's feed URL, AniFlow stands up an RSS
source for the same site. It is deliberately not a normal one: the user may
make it the default, but its name and URL come from the indexer, and it goes
when the indexer goes. Two records describing one site that can drift apart is
worse than a small restriction.

Lives here rather than in the router because both the API and the folder sync
need it: a file dropped into the indexers folder has to bring its feed source
with it, without anyone opening a page.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.indexer import Indexer
from backend.app.models.rss import FeedSource


class FeedAlreadyConfigured(Exception):
    """The user added this site's feed by hand; adopting it would take away
    their ability to edit it."""


def managed_feed(indexer_id: str, db: Session) -> FeedSource | None:
    """The feed source this indexer owns, if it stood one up."""
    return db.scalars(select(FeedSource).where(FeedSource.indexer_id == indexer_id)).first()


def sync_managed_feed(indexer: Indexer, db: Session, *, wanted: bool | None) -> None:
    """Create, update or remove the feed source this indexer owns.

    ``wanted`` None means "leave the decision alone" - used when only the name
    or URL changed, where the existing feed still has to follow along.
    """
    from backend.app.services.indexer_definitions import find_for_url

    definition = find_for_url(indexer.url)
    feed_url = definition.resolve_rss(indexer.url) if definition else None
    existing = managed_feed(indexer.id, db)
    keep = existing is not None if wanted is None else wanted

    if not keep or feed_url is None:
        # Also the path for an indexer whose URL no longer resolves to a file
        # with a feed: keeping a source nothing can explain would leave a feed
        # the user cannot edit *or* account for.
        if existing is not None:
            db.delete(existing)
        return

    if existing is None:
        clash = db.scalars(select(FeedSource).where(FeedSource.url == feed_url)).first()
        if clash is not None:
            raise FeedAlreadyConfigured(feed_url)
        db.add(FeedSource(name=indexer.name, url=feed_url, indexer_id=indexer.id, enabled=True))
        return

    existing.name = indexer.name
    existing.url = feed_url


def sync_managed_feed_or_409(indexer: Indexer, db: Session, *, wanted: bool | None) -> None:
    """The API-facing form: a clash is a 409 the user can act on."""
    try:
        sync_managed_feed(indexer, db, wanted=wanted)
    except FeedAlreadyConfigured as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "That site's feed is already configured as an RSS source. "
                "Remove it there first, or leave this option off."
            ),
        ) from exc


__all__ = [
    "FeedAlreadyConfigured",
    "managed_feed",
    "sync_managed_feed",
    "sync_managed_feed_or_409",
]

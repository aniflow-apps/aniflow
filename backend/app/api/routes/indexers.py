"""Indexer profiles: the sites AniFlow may search.

None are shipped. The user adds a name and a URL, which is the entire
configuration - whatever filtering a site offers in its query string is used
as-is rather than re-modelled here, so no site's categories are baked into
AniFlow.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.indexer import Indexer
from backend.app.services.managed_feeds import managed_feed, sync_managed_feed_or_409
from backend.app.services.config_sync import (
    INDEXERS,
    forget_file,
    sync_from_files,
    write_back,
)

router = APIRouter()


class IndexerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    #: Also stand up an RSS source for this site. Only possible when the
    #: matching definition file supplies a feed URL.
    use_as_rss: bool = False
    # Not AnyHttpUrl: pydantic normalises that type (adding a trailing slash),
    # which would rewrite a search URL the user pasted deliberately.
    url: str = Field(min_length=1, max_length=1000, pattern="^https?://")


class IndexerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    use_as_rss: bool | None = None
    url: str | None = Field(default=None, min_length=1, max_length=1000, pattern="^https?://")
    enabled: bool | None = None
    is_default: bool | None = None


class IndexerResponse(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    is_default: bool
    last_used_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    #: What a search would actually request, with a sample term filled in. The
    #: URL forms differ enough that showing the result is clearer than
    #: explaining the rules.
    example_request: str = ""
    #: Whether this URL can ask for more than the first page.
    supports_paging: bool = False
    #: The definition file governing this URL, when one claims the site. Null
    #: means the generic URL rules are in charge - which is correct for a URL
    #: that carries its own query string.
    definition: str | None = None
    #: Whether a feed source can be stood up from this indexer - true only when
    #: its definition supplies a feed URL. The UI disables the option instead
    #: of inventing one.
    rss_available: bool = False
    #: Whether it currently is. Derived from the feed source existing rather
    #: than stored twice, so the two cannot disagree.
    use_as_rss: bool = False
    rss_source_id: str | None = None
    #: Everything this site lists is adult; what is added from it is marked so.
    adult: bool = False

    model_config = ConfigDict(from_attributes=True)


class IndexerTestResult(BaseModel):
    ok: bool
    #: The URL that answered, which may be a feed variant of the one stored.
    url: str = ""
    items: int = 0
    detail: str | None = None
    #: Set when a conventional feed form of the URL worked and the stored one
    #: did not, so the UI can offer to save the one that works.
    suggested_url: str | None = None


def _payload(indexer: Indexer, db: Session) -> IndexerResponse:
    from backend.app.integrations.search.feed_indexer import build_search_url, supports_paging
    from backend.app.services.indexer_definitions import resolve_search_template

    template, definition = resolve_search_template(indexer.url)
    response = IndexerResponse.model_validate(indexer)
    # Built from the *resolved* template, so the page shows the request that
    # will really be made rather than the address the user typed.
    response.example_request = build_search_url(template, "example title")
    response.supports_paging = supports_paging(template)
    response.definition = definition.name if definition else None
    response.adult = bool(definition and definition.adult)
    response.rss_available = bool(definition and definition.resolve_rss(indexer.url))
    managed = managed_feed(indexer.id, db)
    response.use_as_rss = managed is not None
    response.rss_source_id = managed.id if managed else None
    return response


def _get_or_404(indexer_id: str, db: Session) -> Indexer:
    indexer = db.get(Indexer, indexer_id)
    if indexer is None:
        raise HTTPException(status_code=404, detail="Indexer not found")
    return indexer


def _ensure_single_default(db: Session, *, prefer: Indexer | None = None) -> None:
    """Exactly one default, and only if there is anything to default to."""
    indexers = list(db.scalars(select(Indexer).order_by(Indexer.created_at)))
    if not indexers:
        return
    chosen = prefer or next((item for item in indexers if item.is_default), indexers[0])
    for indexer in indexers:
        indexer.is_default = indexer.id == chosen.id


@router.get("", response_model=list[IndexerResponse])
def list_indexers(db: Session = Depends(get_db)) -> list[IndexerResponse]:
    # Read the folder first: a file dropped in is meant to appear here without
    # an import step, and this is the request that would show it.
    sync_from_files(db)
    order = (Indexer.is_default.desc(), Indexer.name.asc())
    return [_payload(item, db) for item in db.scalars(select(Indexer).order_by(*order))]


# There is no create endpoint on purpose. An indexer is a file in
# <CONFIG_DIR>/indexers, which is what keeps AniFlow itself free of any
# particular site: the project ships none, the user supplies the file, and it
# survives a database wipe because it never lived there.


@router.patch("/{indexer_id}", response_model=IndexerResponse)
def update_indexer(indexer_id: str, request: IndexerUpdate, db: Session = Depends(get_db)) -> IndexerResponse:
    indexer = _get_or_404(indexer_id, db)
    values = request.model_dump(exclude_unset=True)
    if "name" in values:
        indexer.name = (values["name"] or "").strip()
    if "url" in values:
        indexer.url = (values["url"] or "").strip()
        # A changed URL invalidates whatever the last attempt reported.
        indexer.last_error = None
    if "enabled" in values:
        indexer.enabled = bool(values["enabled"])
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An indexer with this name already exists") from exc
    if values.get("is_default"):
        _ensure_single_default(db, prefer=indexer)
    # A renamed or re-pointed indexer drags its feed source along; the flag is
    # only decided when the request actually carries it.
    sync_managed_feed_or_409(indexer, db, wanted=values.get("use_as_rss"))
    # Back to the file, which is where this indexer actually lives. The search
    # mechanics come along unchanged: they are the part the UI cannot edit.
    write_back(db, INDEXERS, Indexer)
    db.commit()
    db.refresh(indexer)
    return _payload(indexer, db)


@router.delete("/{indexer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_indexer(indexer_id: str, db: Session = Depends(get_db)) -> Response:
    indexer = _get_or_404(indexer_id, db)
    # Explicitly, not only by cascade: the feed source belongs to this indexer,
    # and a managed source the user cannot delete must not be able to outlive
    # the thing that explains it.
    managed = managed_feed(indexer_id, db)
    if managed is not None:
        db.delete(managed)
        db.flush()  # before the parent, or the cascade gets there first
    forget_file(INDEXERS, indexer)
    db.delete(indexer)
    db.flush()
    # Deleting the default promotes whatever is left, so searching keeps
    # working without a second visit to this page - and the promotion has to
    # reach that one's file, or the next read puts the old default back.
    _ensure_single_default(db)
    db.flush()
    write_back(db, INDEXERS, Indexer)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{indexer_id}/test", response_model=IndexerTestResult)
def test_indexer(indexer_id: str, db: Session = Depends(get_db)) -> IndexerTestResult:
    """Ask the indexer for something and report exactly what came back.

    A URL that answers with a page rather than a feed is the likeliest
    mistake - a browser address bar holds the search page, not its feed - so
    the conventional feed forms of it are tried too and the working one is
    offered rather than leaving the user with "that did not work".
    """
    from backend.app.integrations.rss.base import FeedError, fetch_feed
    from backend.app.integrations.search.feed_indexer import build_search_url, feed_variants

    from backend.app.services.indexer_definitions import resolve_search_template

    indexer = _get_or_404(indexer_id, db)
    template, definition = resolve_search_template(indexer.url)
    attempts: list[str] = []
    first_error: str | None = None
    # With a definition in charge there is nothing to probe: the file already
    # says how this site is searched, so a failure is a real failure.
    candidates = [template] if definition else feed_variants(indexer.url)
    for candidate in candidates:
        target = build_search_url(candidate, "test")
        attempts.append(target)
        try:
            items = fetch_feed(target)
        except FeedError as exc:
            first_error = first_error or str(exc)
            continue
        indexer.last_used_at = datetime.now(UTC)
        indexer.last_error = None
        db.commit()
        return IndexerTestResult(
            ok=True,
            url=target,
            items=len(items),
            # Never suggested when a file governs the site: the candidate is
            # then the file's own resolved template, and storing that as the
            # indexer's URL would give it a query string - after which no file
            # claims the site and its feed source silently disappears.
            suggested_url=(
                None if definition else (candidate if candidate != indexer.url else None)
            ),
        )

    indexer.last_error = (first_error or "no response")[:250]
    db.commit()
    return IndexerTestResult(
        ok=False,
        url=attempts[0] if attempts else indexer.url,
        detail=first_error or "The indexer did not return a feed",
    )

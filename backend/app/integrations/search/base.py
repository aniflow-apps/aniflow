"""Search provider abstraction for release discovery.

Replaces RSS feed scanning with active search against torrent indexes.
Providers translate AniFlow search requests into provider-specific queries
and return normalized results for the existing matching pipeline.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)


class SearchError(Exception):
    """A search could not be performed."""


@dataclass(frozen=True)
class SearchRequest:
    """What AniFlow wants to search for - provider-agnostic."""
    anime: str                    # Anime name from AniFlow (display_name or local_name)
    subgroup: str | None = None   # From template.subgroups (first entry if list)
    resolution: str | None = None # From template.resolution
    media_source: str | None = None
    censor_preference: str | None = None  # ANY/UNCENSORED/CENSORED


@dataclass(frozen=True)
class SearchResult:
    """Normalized result from any provider."""
    id: str                       # Unique per provider (torrent hash, item id, guid)
    title: str                    # Raw release name
    link: str | None = None       # Torrent file URL
    magnet: str | None = None     # Magnet URI
    size: int | None = None       # Bytes
    seeders: int | None = None
    leechers: int | None = None
    published_at: datetime | None = None
    category: str | None = None   # Provider-specific category


class SearchProvider(Protocol):
    """Interface for release search providers."""
    
    def search(
        self,
        request: SearchRequest,
        *,
        page: int = 1,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search for releases matching the request.
        
        Args:
            request: What to search for
            page: Page number (1-indexed)
            limit: Results per page
        
        Returns:
            List of normalized results. Empty list if no results or error.
        """
        ...
    
    def get_details(self, item_id: str) -> SearchResult | None:
        """Fetch detailed info for a single result (optional)."""
        ...


__all__ = ["SearchError", "SearchRequest", "SearchResult", "SearchProvider"]
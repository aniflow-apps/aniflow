"""AniDB as a source of *names*, read from the title dump (build 89).

Not an API client. AniDB bans automated access to its website and its HTTP
API needs a registered client, so nothing here touches the network: it reads
the title dump AniFlow already downloads weekly - see
``services/anidb_titles.py`` - and that file contains titles and nothing else.

So this provider can answer "what is anime 14758 called?" and cannot answer
anything else. No cover, no episode count, no airing time. That is a real
limitation and it is deliberate: the alternative is guessing from a name, and
guessing from a name is what build 89 was reported for.

It exists for one flow. Shoko Server states the AniDB id of every series it
holds, so adopting one into the watchlist needs no search at all - the id is
already known, and an id cannot be confused with the sequel the way a name
can. Anything the downloaded calendar knows about the same id is preferred,
because that has the cover and the episode count; this is what answers when
nothing has been downloaded for that season.
"""
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.services import anidb_titles


class AnidbDumpProvider:
    """Names for an AniDB id, out of the downloaded dump."""

    name = "anidb"

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        """Names from the dump, for when nothing on the network will answer.

        Build 89 returned nothing here, on the grounds that this provider is
        only ever reached by id. Build 95 changed that, because AniList spent
        a day answering 403 to everything and the add-anime search had no
        fallback that did not need the network. This one needs no network at
        all - it is a file already on disk.

        What comes back is names and an AniDB id. No cover, no episode count,
        no airing date: the file contains titles. An entry added this way is
        honest about that rather than absent.
        """
        aids = anidb_titles.search(query, limit=limit)
        if not aids:
            return []
        found = anidb_titles.titles_for_many(aids)
        # The ranking order of the search, not the dictionary's: the first
        # answer is the best one it has.
        return [
            self._candidate(found[aid]) for aid in aids if aid in found
        ]

    @staticmethod
    def _candidate(titles) -> AnimeCandidate:
        return AnimeCandidate(
            provider="anidb",
            provider_id=str(titles.aid),
            display_name=titles.english or titles.primary,
            title_english=titles.english,
            title_romaji=titles.primary,
            title_native=titles.native,
            aliases=titles.synonyms,
            provider_ids={"anidb": str(titles.aid)},
        )

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        try:
            aid = int(provider_id)
        except (TypeError, ValueError):
            return None
        titles = anidb_titles.titles_for(aid)
        # The English title when AniDB has one, because that is what the rest
        # of the interface shows; the romaji main title otherwise.
        return None if titles is None else self._candidate(titles)

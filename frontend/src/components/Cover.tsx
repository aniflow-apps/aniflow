import { coverUrl, providerCoverUrl } from "../api/client";

/**
 * An anime's artwork.
 *
 * An uploaded cover wins over the provider's: the user chose it deliberately.
 * `has_custom_cover` decides, rather than trying the upload URL and handling
 * a 404, so a watchlist of thirty anime makes no wasted requests.
 */
export function Cover({ anime }: {
  anime: { id?: string; display_name: string; cover_url: string | null; has_custom_cover?: boolean };
}) {
  const source = anime.has_custom_cover && anime.id ? coverUrl(anime.id) : providerCoverUrl(anime.cover_url);
  /* Lazily, and decoded off the main thread (build 88). A season is two
     hundred and sixty cards, and asking for every poster at once made the
     whole grid wait for the ones nobody had scrolled to yet. */
  return source
    ? <img className="cover" src={source} alt="" loading="lazy" decoding="async" />
    : <div className="cover-placeholder">{anime.display_name.slice(0, 1)}</div>;
}

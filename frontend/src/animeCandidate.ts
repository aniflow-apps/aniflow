import type { AnimeDetail, AnimeSearchResult } from "./api/client";

/**
 * A stored anime as the setup dialog's candidate.
 *
 * The dialog was written for a provider search result, and a conversion asks
 * it the same question about an anime already in the library - so the row is
 * presented in the shape it already understands rather than giving it a second
 * one to handle.
 */
export function asCandidate(anime: AnimeDetail): AnimeSearchResult {
  return {
    provider: anime.primary_provider,
    provider_id: anime.primary_provider_id,
    display_name: anime.display_name,
    // The folder this anime already has. A conversion must not rename it, and
    // the dialog seeds its folder field from here.
    folder_hint: anime.local_name,
    // Not part of the question a conversion asks - the dialog only wants the
    // titles and the folder - so they are stated as absent rather than
    // invented.
    air_start_date: null,
    air_slot_at: null,
    popularity: null,
    title_english: anime.title_english,
    title_romaji: anime.title_romaji,
    title_native: anime.title_native,
    aliases: anime.aliases,
    cover_url: anime.cover_url,
    genres: [],
    // The calendar's own fields, which a conversion never asks about.
    studios: [],
    synopsis: null,
    related: null,
    format: anime.format,
    episode_count: anime.episode_count,
    // Not part of the question a conversion asks, like the fields above.
    airing_status: "UNKNOWN",
    is_adult: false,
    // The AniDB id the anime already carries. The dialog's Open AniDB button
    // and its numbering lookup both read it from here (item 49).
    provider_ids: anime.anidb_id ? { anidb: String(anime.anidb_id) } : {},
    in_library: true,
    search_hint: anime.title_romaji ?? anime.display_name,
  };
}

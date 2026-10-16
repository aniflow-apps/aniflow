/**
 * The MyAnimeList page for an id (build 237), or null when there is no usable id.
 *
 * Like `anidbUrl`: only the address of a page for the user's own browser to
 * open, with the id checked to be a plain positive integer so nothing but
 * digits can end up in the address.
 */
export function malUrl(id: number | string | null | undefined): string | null {
  const text = String(id ?? "").trim();
  if (!/^[1-9][0-9]{0,8}$/.test(text)) return null;
  return `https://myanimelist.net/anime/${text}`;
}

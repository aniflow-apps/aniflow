/**
 * The AniDB page for an id (build 188), or null when there is no usable id.
 *
 * Only ever the address of a page for the user's own browser to open. AniFlow
 * must never fetch anidb.net pages itself - a ban is installation-wide - and a
 * link followed by a person is not that. The id is checked to be a plain
 * positive integer so nothing but digits can end up in the address.
 */
export function anidbUrl(id: number | string | null | undefined): string | null {
  const text = String(id ?? "").trim();
  if (!/^[1-9][0-9]{0,8}$/.test(text)) return null;
  return `https://anidb.net/anime/${text}`;
}

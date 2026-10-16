/**
 * Grouping and hiding on the watchlist (build 192, 0.8.5).
 *
 * Pure, so the page stays a rendering of the answers.
 */
import type { AnimeSummary, Library } from "./api/client";
import { foldLetters, longVowels } from "./library";

export interface LibraryGroup {
  /** The library, or null for anime whose library is not known (yet). */
  library: Library | null;
  items: AnimeSummary[];
}

/**
 * The watchlist in library order: the default first (the server sends it so),
 * then the others as listed, empty libraries left out. Anime with no known
 * library go last rather than vanishing - "nothing is hidden by accident" is
 * the rule everywhere on this page. The order inside a group is the order given.
 */
/** Set by the Libraries page: the watchlist opens on this library's group. */
export const FOCUS_LIBRARY_KEY = "aniflow.watchlist.focusLibrary";

/**
 * Remember that the watchlist should open grouped by library, scrolled to one.
 * The sort key is the watchlist's own ("aniflow.watchlist.sort").
 */
export function focusLibraryInWatchlist(libraryId: number): void {
  try {
    window.localStorage.setItem("aniflow.watchlist.sort", "library");
    window.sessionStorage.setItem(FOCUS_LIBRARY_KEY, String(libraryId));
  } catch {
    // Storage blocked: the watchlist just opens as it was.
  }
}

/**
 * What is airing first, then what is coming, then the rest (build 208): the libraries tagged Current
 * season, then Upcoming, then every other in the order the Libraries page gives. Stable inside each.
 */
function shownFirst(libraries: Library[]): Library[] {
  const rank = (library: Library) => (library.fits_season === "current" ? 0 : library.key === "upcoming" ? 1 : 2);
  return libraries
    .map((library, index) => ({ library, index }))
    .sort((a, b) => rank(a.library) - rank(b.library) || a.index - b.index)
    .map((entry) => entry.library);
}

export function groupByLibrary(items: AnimeSummary[], libraries: Library[]): LibraryGroup[] {
  const known = new Set(libraries.map((library) => library.id));
  const groups: LibraryGroup[] = shownFirst(libraries)
    .map((library) => ({ library, items: items.filter((item) => item.library_id === library.id) }))
    .filter((group) => group.items.length > 0);
  const strays = items.filter((item) => item.library_id === null || !known.has(item.library_id));
  if (strays.length > 0) groups.push({ library: null, items: strays });
  return groups;
}

/** Lower case, accents and marks gone: "Ōkami" is found by "okami", "Re:Zero" by "re zero". */
export function foldName(text: string): string {
  return longVowels(
    foldLetters(text)
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim(),
  );
}

/** Whether the watchlist search finds this anime: any name AniFlow holds, not only the one on the card. */
export function matchesName(item: AnimeSummary, query: string): boolean {
  const needle = foldName(query);
  if (!needle) return true;
  return [item.display_name, item.local_name, ...(item.search_names ?? [])].some((name) =>
    foldName(name).includes(needle),
  );
}

/**
 * The libraries the Weekly schedule shows: the ones the user tagged Current season (build 208).
 * `null` while no library is known yet, so the page does not read "nothing tagged" off an empty list.
 */
export function currentLibraryIds(libraries: Library[]): Set<number> | null {
  if (libraries.length === 0) return null;
  return new Set(libraries.filter((library) => library.fits_season === "current").map((library) => library.id));
}

export interface GenreCount {
  genre: string;
  count: number;
}

/** How many anime of the watchlist carry each genre, most first, ties by name. */
export function genreCounts(items: AnimeSummary[]): GenreCount[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    // A genre listed twice on one anime still counts that anime once.
    for (const genre of new Set((item.genres ?? []).map((g) => g.trim()).filter(Boolean))) {
      counts.set(genre, (counts.get(genre) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([genre, count]) => ({ genre, count }))
    .sort((a, b) => b.count - a.count || a.genre.localeCompare(b.genre));
}

/** What the watchlist shows, and how many the adult switch is keeping out. */
export function applyAdultFilter(
  items: AnimeSummary[],
  hideAdult: boolean,
): { shown: AnimeSummary[]; hidden: number } {
  if (!hideAdult) return { shown: items, hidden: 0 };
  const shown = items.filter((item) => !item.is_adult);
  return { shown, hidden: items.length - shown.length };
}

import type { Library } from "./api/client";

/**
 * The libraries as last read, kept for the life of the page (build 207).
 *
 * The watchlist is left and opened again all the time, and it used to start from an empty list and
 * ask: for a moment "By library" had no libraries to group by, and the page redrew when they came.
 * It starts from what it knew and replaces it only if the answer differs.
 */
let known: Library[] = [];

export function knownLibraries(): Library[] {
  return known;
}

export function rememberLibraries(libraries: Library[]): void {
  known = libraries;
}

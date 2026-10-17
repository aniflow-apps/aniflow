/**
 * The rules of the Import folders window (build 254), kept out of the component so they can be tested.
 *
 * A folder is *matched* when AniFlow's AniDB title list settled on it by exact name, and *unmatched* when it did
 * not - the person then picks the anime from a search. Nothing here guesses: a pick is either what the server
 * matched exactly or what the person chose.
 */
import type { ImportCandidate } from "./api/client";

export interface Pick {
  aid: number;
  title: string;
}

/** One folder, told apart from the same name in another library. */
export function rowKey(candidate: { library_id: number; folder: string }): string {
  return `${candidate.library_id}/${candidate.folder}`;
}

/** What the window starts from: the matched folders picked and ticked, the rest left for the person. */
export function seed(candidates: ImportCandidate[]): { picks: Record<string, Pick>; ticked: string[] } {
  const picks: Record<string, Pick> = {};
  const ticked: string[] = [];
  for (const candidate of candidates) {
    if (candidate.anidb_id === null) continue;
    const key = rowKey(candidate);
    picks[key] = { aid: candidate.anidb_id, title: candidate.title ?? `AniDB ${candidate.anidb_id}` };
    if (candidate.tracked_as === null) ticked.push(key);
  }
  return { picks, ticked };
}

/** Whether a row can be imported at all: it names an anime, and that anime is not already on the watchlist. */
export function canImport(candidate: ImportCandidate, picks: Record<string, Pick>): boolean {
  return candidate.tracked_as === null && picks[rowKey(candidate)] !== undefined;
}

/** The folders to import, in the order the window lists them. */
export function toImport(
  candidates: ImportCandidate[],
  picks: Record<string, Pick>,
  ticked: ReadonlySet<string>,
): { candidate: ImportCandidate; pick: Pick }[] {
  return candidates
    .filter((candidate) => ticked.has(rowKey(candidate)) && canImport(candidate, picks))
    .map((candidate) => ({ candidate, pick: picks[rowKey(candidate)] }));
}

/** The keys of ticked folders that point at an AniDB entry another ticked folder points at too. */
export function sharedPicks(
  candidates: ImportCandidate[],
  picks: Record<string, Pick>,
  ticked: ReadonlySet<string>,
): Set<string> {
  const byAid = new Map<number, string[]>();
  for (const { candidate, pick } of toImport(candidates, picks, ticked)) {
    byAid.set(pick.aid, [...(byAid.get(pick.aid) ?? []), rowKey(candidate)]);
  }
  const shared = new Set<string>();
  for (const keys of byAid.values()) if (keys.length > 1) keys.forEach((key) => shared.add(key));
  return shared;
}

/** Episode numbers as a person writes them: 1-3, 5, 7-9. */
export function episodeRanges(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1;
    parts.push(j === i ? String(sorted[i]) : `${sorted[i]}-${sorted[j]}`);
    i = j + 1;
  }
  return parts.join(", ");
}

/**
 * What the search box starts with for an unmatched folder: the folder's name without release decoration
 * ([Group], (2023), underscores and dots between words). Only ever a starting point for the person to edit.
 */
export function searchTextFor(folder: string): string {
  return folder
    .replace(/\[[^\]]*\]|\([^)]*\)/g, " ")
    .replace(/[._]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** What the filter buttons tell apart: AniFlow (or the person) named an anime, nothing did, or it is on the list already. */
export type RowFilter = "all" | "found" | "notfound" | "tracked";

export function rowState(candidate: ImportCandidate, picks: Record<string, Pick>): Exclude<RowFilter, "all"> {
  if (candidate.tracked_as !== null) return "tracked";
  return picks[rowKey(candidate)] !== undefined ? "found" : "notfound";
}

/** How many rows each button would show, for the numbers on them. */
export function countStates(candidates: ImportCandidate[], picks: Record<string, Pick>): Record<RowFilter, number> {
  const counts: Record<RowFilter, number> = { all: candidates.length, found: 0, notfound: 0, tracked: 0 };
  for (const candidate of candidates) counts[rowState(candidate, picks)] += 1;
  return counts;
}

/** The rows a filter button and a typed name leave: the folder's name or the anime it was matched to. */
export function filterRows(
  candidates: ImportCandidate[],
  picks: Record<string, Pick>,
  filter: RowFilter,
  text: string,
): ImportCandidate[] {
  const needle = text.trim().toLocaleLowerCase();
  return candidates.filter((candidate) => {
    if (filter !== "all" && rowState(candidate, picks) !== filter) return false;
    if (needle === "") return true;
    const pick = picks[rowKey(candidate)];
    return (
      candidate.folder.toLocaleLowerCase().includes(needle) ||
      (pick?.title.toLocaleLowerCase().includes(needle) ?? false)
    );
  });
}

/** Tick (or untick) every row that can be imported among the ones on screen, and leave all the others as they were. */
export function tickShown(
  ticked: ReadonlySet<string>,
  shown: ImportCandidate[],
  picks: Record<string, Pick>,
  on: boolean,
): Set<string> {
  const next = new Set(ticked);
  for (const candidate of shown) {
    if (!canImport(candidate, picks)) continue;
    if (on) next.add(rowKey(candidate));
    else next.delete(rowKey(candidate));
  }
  return next;
}

/** How many rows the window draws before "Show more": a library of two thousand folders is not two thousand rows. */
export const PAGE_SIZE = 150;


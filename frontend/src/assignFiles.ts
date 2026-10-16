/**
 * The rules behind "assign the files by hand" (build 213), kept free of React so they can be tested.
 *
 * A selection maps each file to an episode number, or null for "no episode's".
 */
import type { FolderFile } from "./api/client";

export type Selection = Record<string, number | null>;

/** What each file starts as: the episode it is recorded as, else the scan's suggestion, else nothing. */
export function initialSelection(files: readonly FolderFile[]): Selection {
  const selection: Selection = {};
  for (const file of files) selection[file.filename] = file.episode ?? file.suggested ?? null;
  return selection;
}

/** Sorted by name, numbered 1, 2, 3 ...: the quickest answer for a folder whose names sort as its episodes do. */
export function inOrder(files: readonly FolderFile[]): Selection {
  const selection: Selection = {};
  [...files]
    .sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: "base" }))
    .forEach((file, index) => { selection[file.filename] = index + 1; });
  return selection;
}

/** Every file set to nothing. */
export function cleared(files: readonly FolderFile[]): Selection {
  return Object.fromEntries(files.map((file) => [file.filename, null]));
}

/** The episode numbers chosen for more than one file, which a save would refuse. */
export function chosenTwice(selection: Selection): number[] {
  const counts = new Map<number, number>();
  for (const number of Object.values(selection)) {
    if (number !== null) counts.set(number, (counts.get(number) ?? 0) + 1);
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([number]) => number).sort((a, b) => a - b);
}

/** The numbers a file can be given: every episode the anime has, and enough more to number every file. */
export function episodeChoices(known: readonly number[], episodeCount: number | null, fileCount: number): number[] {
  const top = Math.max(episodeCount ?? 0, fileCount, ...known, 0);
  return Array.from({ length: top }, (_, index) => index + 1);
}

/** What a save sends: every file with its choice. */
export function toAssignments(selection: Selection): { filename: string; episode: number | null }[] {
  return Object.entries(selection).map(([filename, episode]) => ({ filename, episode }));
}

/** How many files have an episode. */
export function assignedCount(selection: Selection): number {
  return Object.values(selection).filter((number) => number !== null).length;
}

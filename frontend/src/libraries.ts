/**
 * What the Libraries page says about a library and Shoko (build 192, 0.8.5).
 *
 * Shoko only leaves a file where AniFlow put it when the folder is one it is
 * watching, so a library over an unwatched folder is a problem the user can
 * fix in two clicks - if something tells them. Pure, so it is testable
 * without a page.
 */
import type { Library, LibraryRefresh, ShokoImportFolder, ShokoImportFolders } from "./api/client";

export type ShokoWarning = "notLinked" | "notWatching" | "gone" | null;

/**
 * The warning a library deserves, or null.
 *
 * Only in Shoko mode, and only when Shoko answered: an unreachable Shoko is
 * its own, louder, message, and repeating it on every row would bury it.
 */
export function shokoWarning(
  library: Pick<Library, "shoko_folder_id"> & { has_folder?: boolean },
  shoko: ShokoImportFolders | null,
  shokoMode: boolean,
): ShokoWarning {
  if (!shokoMode || !shoko || !shoko.reachable) return null;
  // Upcoming has no folder on any disk, so there is nothing for Shoko to watch.
  if (library.has_folder === false) return null;
  if (library.shoko_folder_id === null) return "notLinked";
  const folder = shoko.folders.find((f) => f.id === library.shoko_folder_id);
  if (!folder) return "gone";
  return folder.watching ? null : "notWatching";
}

/** "Source", "Destination" or "Both", as the page shows it; Shoko's word, kept. */
export function dropTypeLabel(dropType: string | null): string {
  return dropType ?? "—";
}

const trimmed = (path: string) => path.replace(/\/+$/, "");

export interface FolderRelations {
  /** Libraries over exactly the same folder. */
  shares: string[];
  /** Libraries whose folder holds this one. */
  inside: string[];
  /** Libraries whose folder is held by this one. */
  contains: string[];
}

/** How a library's folder sits next to the other libraries' folders (names only; no folder, no relation). */
export function folderRelations(
  library: Pick<Library, "id" | "folder" | "has_folder">,
  all: Pick<Library, "id" | "name" | "folder" | "has_folder">[],
): FolderRelations {
  const result: FolderRelations = { shares: [], inside: [], contains: [] };
  if (!library.has_folder) return result;
  const mine = trimmed(library.folder);
  for (const other of all) {
    if (other.id === library.id || !other.has_folder) continue;
    const theirs = trimmed(other.folder);
    if (theirs === mine) result.shares.push(other.name);
    else if (mine.startsWith(`${theirs}/`)) result.inside.push(other.name);
    else if (theirs.startsWith(`${mine}/`)) result.contains.push(other.name);
  }
  return result;
}

/** Whether a Shoko import folder can hold a library: Destination or Both. Source (and None) cannot. */
export function canBeALibrary(dropType: string | null): boolean {
  const kind = (dropType ?? "").toLowerCase();
  return kind === "destination" || kind === "both";
}

/** The library already linked to a Shoko folder, if any. */
export function libraryOfShokoFolder(libraries: Pick<Library, "shoko_folder_id" | "name">[], folderId: number | null) {
  if (folderId === null) return null;
  return libraries.find((library) => library.shoko_folder_id === folderId) ?? null;
}

/** What the Add library window opens with for a Shoko folder. */
export interface LibraryPreset {
  name: string;
  folder: string;
  shokoFolderId: number | null;
  adult: boolean;
  season: "current" | "past" | "";
}

/**
 * A starting point for a library made from a Shoko folder - suggestions only,
 * the user confirms every field. The name comes from Shoko's folder name; an
 * adult-sounding name ticks Adult, a current-season one tags Current. The
 * folder is filled in by the caller once it has found it on AniFlow's side.
 */
export function presetFromShokoFolder(folder: Pick<ShokoImportFolder, "id" | "name" | "path">, aniflowFolder = ""): LibraryPreset {
  const text = `${folder.name ?? ""} ${folder.path ?? ""}`;
  return {
    name: (folder.name ?? "").trim(),
    folder: aniflowFolder,
    shokoFolderId: folder.id,
    adult: /ecchi|hentai|adult|xxx/i.test(text),
    season: /current[\s_-]*season|airing/i.test(text) ? "current" : "",
  };
}

/**
 * Where AniFlow might see a Shoko folder: the same path (when AniFlow's /media
 * is the tree Shoko sees), or a top-level mount named like it. Tried in order.
 */
export function folderCandidates(folder: Pick<ShokoImportFolder, "name" | "path">): string[] {
  const path = (folder.path ?? "").replace(/\/+$/, "");
  const last = path.split("/").filter(Boolean).pop() ?? "";
  const named = [last, (folder.name ?? "").trim()].filter(Boolean).map((n) => `/${n}`);
  return [...new Set([path, ...named].filter((c) => c.startsWith("/") && c.length > 1))];
}

/**
 * The folders from just below ``root`` down to ``target``, outermost first.
 *
 * What the folder picker opens to show a folder that is already chosen.
 * Empty when ``target`` is not under ``root``.
 */
export function pathChain(root: string, target: string): string[] {
  const base = root.replace(/\/+$/, "");
  if (!target.startsWith(`${base}/`)) return [];
  const parts = target.slice(base.length).split("/").filter(Boolean);
  return parts.map((_, index) => `${base}/${parts.slice(0, index + 1).join("/")}`);
}

/** What AniFlow knows about a show that a library's "fits" tags can be held against. */
export interface LibraryFacts {
  adult: boolean;
  /** `current` while it airs or is about to, `past` once it has finished; null when unknown. */
  season: "current" | "past" | null;
}

/** The facts of a search result: is it adult, and does it air now or has it finished. */
export function factsOf(candidate: { airing_status?: string }, adult: boolean): LibraryFacts {
  const status = candidate.airing_status ?? "UNKNOWN";
  return {
    adult,
    season: status === "AIRING" || status === "NOT_YET_AIRED" ? "current" : status === "FINISHED" ? "past" : null,
  };
}

export type LibraryFit = "fits" | "neutral" | "mismatch";

export interface RankedLibrary {
  library: Library;
  fit: LibraryFit;
}

/**
 * Every library the user may add into, the best fit first.
 *
 * A library fits when something it asks for matches and nothing it asks for
 * contradicts; one with no tags is neutral; one that contradicts is last but
 * still listed - a suggestion is an order, never a filter. A fact the source
 * did not give (an unknown season) neither matches nor contradicts. The server
 * sends the default first, and ties keep that order.
 *
 * A library with no folder (Upcoming) is for anime that have not started: it
 * is offered, and put first, only when the entry being added is a future
 * release, and never otherwise - an entry with files needs a folder (build 198).
 */
export function rankLibraries(libraries: Library[], facts: LibraryFacts, forFuture = false): RankedLibrary[] {
  // A library tagged with a season claims it; one with no tag only takes the seasons nobody claims.
  const claimed = new Set(libraries.map((library) => library.fits_season).filter(Boolean));
  const ranked = libraries
    .filter((library) => library.has_folder || forFuture)
    .map((library, index) => {
      let matched = 0;
      let contradicted = 0;
      const judge = (known: boolean, ok: boolean) => {
        if (!known) return;
        if (ok) matched += 1;
        else contradicted += 1;
      };
      if (library.fits_adult) judge(true, (library.fits_adult === "adult") === facts.adult);
      if (library.fits_season) judge(facts.season !== null, library.fits_season === facts.season);
      else if (facts.season !== null && claimed.has(facts.season)) judge(true, false);
      // A future release belongs in the library that has no folder, whatever else it says.
      const isUpcoming = forFuture && !library.has_folder;
      const fit: LibraryFit = isUpcoming ? "fits" : contradicted > 0 ? "mismatch" : matched > 0 ? "fits" : "neutral";
      return { library, fit, matched: isUpcoming ? 99 : matched, index };
    });
  const order: Record<LibraryFit, number> = { fits: 0, neutral: 1, mismatch: 2 };
  ranked.sort((a, b) => order[a.fit] - order[b.fit] || b.matched - a.matched || a.index - b.index);
  return ranked.map(({ library, fit }) => ({ library, fit }));
}

/** What the picker opens on: the best fit if there is one, else the default. */
export function suggestedLibrary(ranked: RankedLibrary[]): Library | null {
  const best = ranked.find((entry) => entry.fit === "fits");
  if (best) return best.library;
  return ranked.find((entry) => entry.library.is_default)?.library ?? ranked[0]?.library ?? null;
}

/** Whether a refresh found anything to say besides "all as it was". */
export function refreshHasNews(result: LibraryRefresh): boolean {
  return (
    result.moved_in.length + result.moved_out.length + result.missing.length + result.linked.length +
      result.unknown_folders.length + result.problems.length + result.files_gone > 0
  );
}

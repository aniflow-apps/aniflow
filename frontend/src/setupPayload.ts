/**
 * What the add-anime dialog sends, decided apart from the dialog (build 93).
 *
 * Three buttons share one form: add a new anime, convert a tracker entry into
 * a real one, and change the release of an existing one. They send three
 * different payloads built from the same handful of values, and every bug this
 * dialog has had was in that arithmetic rather than on the screen:
 *
 * * the release change sent a JSON body with no content type (build 88);
 * * the release change never sent the search query, so the server had nothing
 *   to look for and every episode arrived with no releases (build 91) - and
 *   the fix for it was reported as landed when it had not, which is what
 *   moving this out of the component is for;
 * * adding while the search was still running had to keep working from the
 *   typed filters alone (verified in build 91).
 *
 * None of that needs a rendered dialog to test. It needs the answer to "given
 * what the user chose, what goes over the wire" - so that is a function.
 */

import type { BatchChoice } from "./api/client";

/** One row of the dialog's release list, as far as this needs to know. */
export interface SelectedRelease {
  subgroup?: string | null;
  resolution?: string | null;
  /** The folder name the row implies, when the server worked one out. */
  folderName?: string | null;
  /** The release title itself. */
  title?: string | null;
  /**
   * Which copy of each episode this row is (build 97): ["uncensored",
   * "dual-audio"], or empty when the group publishes one copy per episode.
   *
   * Sent and stored, because a row you can pick and not store is the lie
   * build 94 was reported for: the queue would be free to take either copy
   * afterwards, and the dialog would have shown you a choice it did not keep.
   */
  edition?: string[] | null;
  /**
   * What the other copies are recognised by (build 98). Needed as well as
   * `edition`: the plain copy carries everything the dual-audio row asks for
   * minus one tag, so requiring alone let the wrong file in.
   */
  editionNot?: string[] | null;
}

/** Everything the dialog knows when the confirm button is pressed. */
export interface SetupChoice {
  /** The folder name, exactly as typed. Never overwritten by a selection. */
  folderName: string;
  /** The name being searched for, which the user may have edited. */
  searchName: string;
  /** Extra search terms the user ticked. */
  extras: readonly string[];
  /**
   * Tickets for the scan already run on the selected row (build 100).
   *
   * With them the server writes down the releases the dialog showed instead
   * of searching again - the question that prompted this was the right one:
   * "the list is already there and the files exist". Empty is not an error;
   * it means the server searches, which is what every build up to 99 did.
   */
  scanIds?: readonly string[];
  /**
   * How far the selected group's numbering runs past this season's episode 1
   * (build 112). 66 for Re:Zero season 4 under SubsPlease, whose first file
   * for it is called 67; zero - the default - for every ordinary case.
   */
  episodeOffset?: number;
  /** The season's length, when the user typed one over the provider's (176). */
  episodeCount?: number | null;
  /**
   * AniDB's id for this season, when this screen knows it (build 116).
   *
   * It generally does, because working out the numbering above is what
   * established it. Sent rather than resolved again on the server: the server
   * would have only the titles to go on, and for a sequel those resolve to
   * the prequel as readily as to the season being added. It is also the id
   * Shoko Server states for everything it holds.
   */
  anidbId?: number | null;
  /** The row the user picked, if the list had produced one yet. */
  selected?: SelectedRelease | null;
  /** The subgroup filter box, which may hold a comma-separated list. */
  subgroupFilter: string;
  /** The quality filter box. */
  resolutionFilter: string;
}

/**
 * The subgroup to ask for: the selected row's, else the first typed filter.
 *
 * The filter is the fallback because adding an anime must never depend on a
 * search having answered - with no indexer at all the typed choice is still a
 * choice worth keeping.
 */
export function chosenSubgroup(choice: SetupChoice): string | null {
  const selected = choice.selected?.subgroup;
  if (selected) return selected;
  return choice.subgroupFilter.split(",")[0]?.trim() || null;
}

export function chosenResolution(choice: SetupChoice): string | null {
  return choice.selected?.resolution ?? (choice.resolutionFilter || null);
}

/**
 * The edition to pin: the selected row's, and nothing otherwise.
 *
 * No fallback, unlike the subgroup and the quality. Those have a typed filter
 * behind them that is a choice in its own right; an edition is only ever
 * derived from the files that came back, so with no row selected there is
 * nothing to pin - and pinning a guess would refuse every copy.
 */
export function chosenEdition(choice: SetupChoice): string[] {
  return [...(choice.selected?.edition ?? [])];
}

/** The copies this row is *not* - see SelectedRelease.editionNot. */
export function refusedEdition(choice: SetupChoice): string[] {
  return [...(choice.selected?.editionNot ?? [])];
}

/**
 * What the server should ask the indexer once the anime exists.
 *
 * The name and the extra terms only: the server appends the group and the
 * quality itself, and that search is specific enough to return every episode
 * rather than the sample this dialog had to work from.
 */
/** The tickets to present, if the row was scanned. */
export function scanTickets(choice: SetupChoice): string[] {
  return [...(choice.scanIds ?? [])];
}

export function searchQuery(choice: SetupChoice): string {
  return [choice.searchName.trim(), ...choice.extras].filter(Boolean).join(" ");
}

/**
 * The release name to store: the selected row's, never the folder name.
 *
 * Null when nothing was selected, because a folder name the user typed is not
 * a name any group publishes under - and the stored release name is what all
 * later matching is done against. A name that matches no release leaves an
 * anime that finds nothing, which is how a season sat empty for days.
 */
export function releaseName(choice: SetupChoice): string | null {
  return choice.selected?.folderName ?? choice.selected?.title ?? null;
}

/** The body of a "change the release" request. */
export function reacquireBody(
  choice: SetupChoice,
  mode: string,
  indexerId?: string | null,
): {
  mode: string;
  subgroups: string[];
  resolution: string | null;
  edition: string[];
  edition_not: string[];
  release_name: string | null;
  local_name: string;
  search_query: string | null;
  indexer_id?: string;
} {
  const subgroup = chosenSubgroup(choice);
  return {
    mode,
    subgroups: subgroup ? [subgroup] : [],
    resolution: chosenResolution(choice),
    edition: chosenEdition(choice),
    edition_not: refusedEdition(choice),
    release_name: releaseName(choice),
    /* The label follows the new group (build 116). The folder field this
       comes from already follows the selected row until somebody types in
       it, so swapping SubsPlease for Erai-raws carries Erai-raws' spelling
       of the season - and an entry named after the group it no longer
       follows was the same bug as a release name left unchanged.

       The storage folders are not sent, deliberately: they are where the
       files already are, and in Shoko Server mode they are Shoko's naming.
       Renaming those would orphan everything filed under them. */
    local_name: choice.folderName,
    // The field the add path always sent and this one did not. Without it the
    // server falls back to the stored release name, which is the name being
    // replaced - so the search either found the old group or nothing at all.
    search_query: searchQuery(choice) || null,
    ...(indexerId ? { indexer_id: indexerId } : {}),
  };
}

/** The setup half of an "add this anime" request. */
export function addSetup(
  choice: SetupChoice,
  extra: { templateId?: string | null; feedSourceId?: string | null },
): {
  subgroup: string | null;
  resolution: string | null;
  edition: string[];
  edition_not: string[];
  template_id: string | null;
  feed_source_id: string | null;
  search_query: string | null;
  scan_ids: string[];
  release_name: string | null;
  episode_offset: number;
  anidb_id: number | null;
  episode_count?: number;
} {
  return {
    subgroup: chosenSubgroup(choice),
    resolution: chosenResolution(choice),
    edition: chosenEdition(choice),
    edition_not: refusedEdition(choice),
    template_id: extra.templateId ?? null,
    feed_source_id: extra.feedSourceId || null,
    search_query: searchQuery(choice) || null,
    scan_ids: scanTickets(choice),
    release_name: releaseName(choice),
    episode_offset: Math.max(0, Math.trunc(choice.episodeOffset ?? 0)),
    /* So the anime is stored with AniDB's id and AniDB's own names (build
       116). Both are wanted for every anime that has them, and neither can
       be worked out afterwards as reliably as it can be here. */
    anidb_id: choice.anidbId ?? null,
    ...(choice.episodeCount ? { episode_count: choice.episodeCount } : {}),
  };
}

/**
 * The body of a "fix the missing episodes" request (build 117).
 *
 * The same choice object as the other three, and deliberately the smallest
 * of the four: this one changes no profile and clears nothing. It says what
 * to search for and what the row it was matched against looks like, and the
 * server attaches whatever the episodes with nothing behind them can use.
 */
export function fixMissingBody(
  choice: SetupChoice,
  /** A batch to take the missing episodes out of, when the group only has one (build 213). */
  batch: BatchChoice | null = null,
  /** The indexer the dialog searched, when it should be remembered (item 44). */
  indexerId?: string | null,
): {
  search_query: string | null;
  subgroup: string | null;
  resolution: string | null;
  edition: string[];
  edition_not: string[];
  scan_ids: string[];
  release_name: string | null;
  queue: boolean;
  batch?: BatchChoice;
  indexer_id?: string;
} {
  return {
    search_query: searchQuery(choice) || null,
    subgroup: chosenSubgroup(choice),
    resolution: chosenResolution(choice),
    edition: chosenEdition(choice),
    edition_not: refusedEdition(choice),
    scan_ids: scanTickets(choice),
    release_name: releaseName(choice),
    // The button says "fix". A fix that stops one step short of the download
    // is a half-fix - see the answer that settled it.
    queue: true,
    ...(batch ? { batch } : {}),
    ...(indexerId ? { indexer_id: indexerId } : {}),
  };
}

/** The body of a "make this tracker entry a real one" request. */
export function convertBody(
  choice: SetupChoice,
  extra: { templateId?: string | null; feedSourceId?: string | null; libraryId?: number | null; indexerId?: string | null },
): {
  release_name: string;
  local_name: string;
  subgroup: string | null;
  resolution: string | null;
  edition: string[];
  edition_not: string[];
  template_id: string | null;
  feed_source_id: string | null;
  episode_count?: number;
  library_id?: number;
  search_query: string | null;
  scan_ids: string[];
  episode_offset: number;
  indexer_id?: string;
} {
  return {
    // The one payload where the folder name is an acceptable release name:
    // converting an entry that has none is better than leaving it with none.
    release_name: releaseName(choice) ?? choice.folderName,
    local_name: choice.folderName,
    subgroup: chosenSubgroup(choice),
    resolution: chosenResolution(choice),
    edition: chosenEdition(choice),
    edition_not: refusedEdition(choice),
    template_id: extra.templateId ?? null,
    feed_source_id: extra.feedSourceId || null,
    // What the dialog searched and found, as an add sends it (build 208): the server writes the
    // group's releases onto the episodes. Without these a converted entry had a profile and nothing else.
    search_query: searchQuery(choice) || null,
    scan_ids: scanTickets(choice),
    episode_offset: Math.max(0, Math.trunc(choice.episodeOffset ?? 0)),
    ...(choice.episodeCount ? { episode_count: choice.episodeCount } : {}),
    // Now that it will have files it needs a library with a folder (build 198).
    ...(extra.libraryId ? { library_id: extra.libraryId } : {}),
    ...(extra.indexerId ? { indexer_id: extra.indexerId } : {}),
  };
}

/**
 * How a row relates to the profiles you saved, and which quality is better.
 *
 * What is left of a larger module. Until build 82 the add-anime dialog was
 * filter-first: the subgroup and quality boxes were pre-filled from the
 * default profile and sent to the indexer, so a filter matching nothing had to
 * *fall back* to showing everything, and the selection had to be recomputed
 * whenever that happened. That machinery lived here.
 *
 * Build 82 searches wide - each name with each quality, merged - and filters
 * the answer in the browser, where a filter that matches nothing simply means
 * nothing. So the fallback and the selection repair went away, and what
 * remains is the ranking and the ordering, which both screens still use.
 */

/** Best first. Also decides which row wins when several qualities are offered. */
export const RESOLUTIONS = ["4320p", "2160p", "1440p", "1080p", "720p", "540p", "480p"];

/** Group names are written every which way, so compare them loosely. */
export function normalizeGroup(name: string | null | undefined): string {
  return (name ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

export function qualityRank(resolution: string | null): number {
  const index = RESOLUTIONS.indexOf(resolution ?? "");
  // Unknown quality sorts last: it cannot be claimed to be better than 480p.
  return index === -1 ? RESOLUTIONS.length : index;
}

/**
 * How a row relates to the release profiles you have saved.
 *
 * 0 - a group in your default profile. What you normally want, so it is green,
 *     sorted first, and picked for you.
 * 1 - a group in one of your other profiles. Known-good but not preferred, so
 *     it is yellow and sits behind the green rows.
 * 2 - everything else, plain.
 */
export function rankOf(
  row: { subgroup: string | null },
  mine: Set<string>,
  known: Set<string>,
): 0 | 1 | 2 {
  const name = normalizeGroup(row.subgroup);
  if (name && mine.has(name)) return 0;
  if (name && known.has(name)) return 1;
  return 2;
}

/**
 * 0 when the row is the quality the default profile asks for, 1 otherwise (build 209).
 *
 * Within one rank the best quality used to win, so a default profile of "SubsPlease 720p" showed the
 * 1080p row first and picked it for you. The default is what you asked for; a better quality is still
 * listed, just not on top.
 */
export function wantedQuality(row: { resolution: string | null }, wanted: string | null | undefined): 0 | 1 {
  return wanted && row.resolution === wanted ? 0 : 1;
}

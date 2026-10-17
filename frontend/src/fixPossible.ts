/**
 * Whether "Fix the missing episodes" has anything to work with (build 207).
 *
 * Only once the chosen group's scan has come back with at least one single episode.
 * A scan still running, one that failed, and one that found only batches all say no.
 */
export function fixIsPossible(
  scan: { episodes: number[] } | null | undefined,
  scanning: boolean,
  /** How many missing episodes the group's batch would give (build 213). A batch-only group is no longer a dead end. */
  batchEpisodes = 0,
): boolean {
  return !scanning && Boolean(scan) && ((scan?.episodes.length ?? 0) > 0 || batchEpisodes > 0);
}

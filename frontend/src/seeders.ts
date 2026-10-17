/**
 * Is anybody sharing this release? (build 247)
 *
 * *"Mark which one is active - more than one user - and which are dead: 0 or unknown."*  Marked only, never
 * ranked: the list is ordered by group and quality, and a release with no seeders today may have some
 * tomorrow. A count is a snapshot from the tracker's own listing, and some trackers publish none, which is
 * "unknown" and not "dead".
 */
export type SeedState = "alive" | "dead" | "unknown";

export function seedState(seeders: number | null | undefined): SeedState {
  if (seeders === null || seeders === undefined || Number.isNaN(seeders)) return "unknown";
  return seeders > 0 ? "alive" : "dead";
}

/** The best count among a row's releases, or null when none of them carries one. */
export function bestSeeders(releases: readonly { seeders: number | null }[]): number | null {
  let best: number | null = null;
  for (const release of releases) {
    if (release.seeders === null || release.seeders === undefined) continue;
    if (best === null || release.seeders > best) best = release.seeders;
  }
  return best;
}

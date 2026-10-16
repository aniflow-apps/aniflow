/**
 * The rules for taking single episodes out of a batch (build 213, docs/batch-episodes-plan.md), free of React.
 *
 * A group that only publishes the season as one torrent used to be a dead end in Choose release. The
 * dialog now reads what is inside the batch and says which of its files are this anime's missing
 * episodes; these are the decisions it makes from the answer.
 */
import type { BatchPreview, ScannedRelease } from "./api/client";

/** The batch to read: the best-seeded release of the scan that is a whole-season torrent and can be asked about. */
export function pickBatch(releases: readonly ScannedRelease[] | null | undefined): ScannedRelease | null {
  const batches = (releases ?? []).filter((release) => release.batch && release.id);
  if (batches.length === 0) return null;
  return [...batches].sort((a, b) => (b.seeders ?? 0) - (a.seeders ?? 0))[0];
}

/**
 * The episodes to take from the batch: what it has that the anime is missing, minus what single files of the
 * same group already cover. Singles are preferred; the batch only fills the gaps they leave.
 *
 * `scanEpisodes` are the group's own numbers and `offset` is how far they run past this season's episode 1.
 */
export function batchEpisodes(preview: BatchPreview | null, scanEpisodes: readonly number[], offset = 0): number[] {
  if (!preview) return [];
  const covered = new Set(scanEpisodes.map((number) => number - offset));
  return preview.wanted.filter((number) => !covered.has(number));
}

export interface BatchRow {
  episode: number;
  name: string;
  size: number;
}

/** The files that will be downloaded, one per episode, in episode order. */
export function batchRows(preview: BatchPreview | null, episodes: readonly number[]): BatchRow[] {
  if (!preview) return [];
  const wanted = new Set(episodes);
  return preview.files
    .filter((file) => file.status === "wanted" && file.episode !== null && wanted.has(file.episode))
    .map((file) => ({ episode: file.episode as number, name: file.name, size: file.size }))
    .sort((a, b) => a.episode - b.episode);
}

/** How many files of the batch are left alone. */
export function leftAlone(preview: BatchPreview | null, episodes: readonly number[]): number {
  if (!preview) return 0;
  return preview.files.length - batchRows(preview, episodes).length;
}

/** Whether the batch could not be used because the answer said so, as opposed to simply having nothing to give. */
export function batchGivesNothing(preview: BatchPreview | null, episodes: readonly number[]): boolean {
  return preview !== null && episodes.length === 0;
}

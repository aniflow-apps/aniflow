/**
 * Reading a selection scan, apart from the dialog that shows it (build 95).
 *
 * The same reason setupPayload.ts exists: every bug this dialog has had was in
 * the arithmetic, not on the screen. The one being fixed here is the largest
 * of them - the names the narrow scan is asked under. The dialog searched two
 * (the box's name and the provider's other spelling) and the server searched
 * one, so an anime whose group publishes under the *other* name was found by
 * the dialog and lost by the server, and the episode list came up empty.
 *
 * So "which names go over the wire" is a function with a test rather than an
 * expression inside a component.
 */
import type { ScanQuery, ScannedRelease, SelectionScan } from "./api/client";

/**
 * The names to scan under: the box's name, plus the provider's other spelling.
 *
 * Exactly the names the broad search used, which is the point - the narrow
 * scan has to be able to find everything the broad one found, or the number in
 * the box is a promise the server cannot keep.
 *
 * An edited name replaces them both: it is an instruction, and quietly
 * searching two other things as well would make the answer impossible to
 * reason about. Mirrors the `terms` rule in AnimeSetupDialog.
 */
export function scanQueries(
  searchName: string,
  otherName: string | null | undefined,
  options: { nameEdited?: boolean } = {},
): string[] {
  const base = searchName.trim();
  if (!base) return [];
  if (options.nameEdited || !otherName?.trim()) return [base];
  const other = otherName.trim();
  return other === base ? [base] : [base, other];
}

/**
 * The searches that actually turned this group up, most productive first (build 212).
 *
 * "Search Full" and the other modes ask the tracker with several shortened and alternative names, and
 * the box holds only the long provider title - "Watashi ga Koibito ni Nareru Wake Nai jan, Muri Muri!
 * (*Muri ja Nakatta!?): Next Shine!" - which a tracker cannot match at all. Scanning the selected group
 * with the box's name found nothing for a group that was plainly there. The terms that found its
 * releases are the questions the tracker is known to answer.
 */
export function termsThatFound(foundBy: readonly (readonly string[])[], limit = 3): string[] {
  const counts = new Map<string, number>();
  for (const terms of foundBy) {
    for (const term of terms) {
      const cleaned = term.trim();
      if (cleaned) counts.set(cleaned, (counts.get(cleaned) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([term, count], index) => ({ term, count, index }))
    .sort((a, b) => b.count - a.count || a.index - b.index)
    .slice(0, limit)
    .map((entry) => entry.term);
}

/** Episode numbers as ranges: [1,2,3,5,6] -> "1-3, 5-6". */
export function episodeRanges(numbers: readonly number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  if (!sorted.length) return "";
  const parts: string[] = [];
  let start = sorted[0];
  let previous = sorted[0];
  for (const number of sorted.slice(1)) {
    if (number === previous + 1) {
      previous = number;
      continue;
    }
    parts.push(start === previous ? `${start}` : `${start}-${previous}`);
    start = number;
    previous = number;
  }
  parts.push(start === previous ? `${start}` : `${start}-${previous}`);
  return parts.join(", ");
}

/** How many releases each verdict accounts for, most first. */
export function reasonCounts(releases: readonly ScannedRelease[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const release of releases) {
    if (release.accepted) continue;
    const reason = release.reason ?? "unknown";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/**
 * The one thing worth saying about a scan that found nothing.
 *
 * Ordered by what the user can do about it. No indexer and an unreachable one
 * are not the profile's fault and must not read as "this group has nothing";
 * after that, the most common verdict is the answer.
 */
export function emptyReason(scan: SelectionScan): string | null {
  if (scan.episodes.length) return null;
  if (scan.no_indexer) return "no-indexer";
  if (!scan.reached_provider) return "unreachable";
  if (!scan.releases.length) return "nothing-found";
  return reasonCounts(scan.releases)[0]?.[0] ?? "nothing-found";
}

/**
 * The name that found the releases, when a different one found none.
 *
 * This is the Makina-san bug made visible: the box offered the English title,
 * SubsPlease publishes under the romaji one. Null when every name found
 * something, or when none did - neither is a finding about a name.
 */
export function nameThatFoundIt(asked: readonly ScanQuery[]): string | null {
  if (asked.length < 2) return null;
  const productive = asked.filter((outcome) => outcome.found > 0);
  const barren = asked.filter((outcome) => outcome.found === 0 && !outcome.error);
  if (productive.length !== 1 || !barren.length) return null;
  return productive[0].term;
}

/** Bytes as something short enough for a table cell. */
export function shortSize(bytes: number | null): string {
  if (!bytes || bytes < 0) return "";
  const giB = bytes / 1024 ** 3;
  if (giB >= 1) return `${giB.toFixed(1)} GiB`;
  return `${Math.round(bytes / 1024 ** 2)} MiB`;
}

/**
 * How a group numbers its episodes, and whether that is this season's count.
 *
 * Subgroups disagree about this and the disagreement is invisible until an
 * episode list looks wrong. Erai-raws restarts at 1 every season and writes
 * the season into the name; SubsPlease never writes a season and keeps
 * counting - so season four of a show with three behind it is "51" to one of
 * them and "01" to the other. Both are correct. They are not interchangeable.
 *
 * Reported, never corrected. Renumbering would mean knowing how many episodes
 * every earlier season had, from a provider that agrees with the subgroup
 * about where the seasons divide, and being wrong there renames a library.
 * Saying which numbering a row uses is enough to pick the right row.
 *
 * In the browser rather than on the server because it is presentation, and
 * because the dialog asks one name at a time and merges the answers: derived
 * on the server it would describe a fraction of the result.
 */
export interface Numbering {
  first: number | null;
  last: number | null;
  count: number;
  /** Numbers between first and last that nothing offered. */
  missing: number[];
  /** True when the numbers run past what this season is said to hold. */
  absolute: boolean;
  /** first - 1, when the numbering really is absolute. */
  offset: number | null;
}

export function numbering(
  episodes: readonly number[],
  episodeCount: number | null | undefined,
): Numbering {
  const ordered = [...new Set(episodes)].sort((a, b) => a - b);
  if (!ordered.length) {
    return { first: null, last: null, count: 0, missing: [], absolute: false, offset: null };
  }
  const first = ordered[0];
  const last = ordered[ordered.length - 1];
  const present = new Set(ordered);
  const missing: number[] = [];
  for (let number = first; number <= last; number += 1) {
    if (!present.has(number)) missing.push(number);
  }
  // Both halves are required. Twelve episodes numbered 51 to 62 against a
  // stated count of 12 is a group counting from the start of the franchise; a
  // provider count that is simply too low - a cour split after the fact - trips
  // only the "runs past it" half, and renumbering that would be wrong.
  const absolute = Boolean(episodeCount) && first > episodeCount! && last > episodeCount!;
  return {
    first,
    last,
    count: ordered.length,
    missing,
    absolute,
    offset: absolute ? first - 1 : null,
  };
}

/**
 * Two answers to the same question, merged.
 *
 * The dialog asks one name at a time so there is something to show progress
 * with, which means the answers arrive in pieces. Releases are keyed by their
 * name: a tracker gives one file one name, whichever query found it.
 */
export function mergeScans(into: SelectionScan | null, next: SelectionScan): SelectionScan {
  if (!into) return next;
  const byName = new Map(into.releases.map((release) => [release.raw_name, release]));
  for (const release of next.releases) {
    if (!byName.has(release.raw_name)) byName.set(release.raw_name, release);
  }
  const releases = [...byName.values()];
  return {
    scan_ids: [...into.scan_ids, ...next.scan_ids],
    releases: [
      ...releases.filter((release) => release.accepted),
      ...releases.filter((release) => !release.accepted),
    ],
    episodes: [
      ...new Set(
        releases
          .filter((release) => release.accepted && release.number !== null)
          .map((release) => release.number as number),
      ),
    ].sort((a, b) => a - b),
    asked: [...into.asked, ...next.asked],
    pages: Math.max(into.pages, next.pages),
    failures: [...into.failures, ...next.failures],
    // One name reaching the tracker is enough to say it was reachable; one
    // finding no indexer means there is none for any of them.
    reached_provider: into.reached_provider || next.reached_provider,
    no_indexer: into.no_indexer && next.no_indexer,
    stored_rules: into.stored_rules ?? next.stored_rules,
  };
}

/** One row of the files table: an episode, and whatever was found for it. */
export interface EpisodeRow {
  number: number;
  files: ScannedRelease[];
}

/**
 * The accepted files as one row per episode, in order.
 *
 * Every episode from the first to the last that was found, plus the ones the
 * metadata source says are still to come - with nothing in them. An ongoing
 * show is the case that matters: seeing episodes 22 to 24 empty says the group
 * has not got there yet, where skipping them says nothing and filling them
 * with a refused file (which is what build 97 did) says something false.
 *
 * Never *fewer* rows than there are files: a group numbering past the stated
 * count still gets a row for each of its episodes.
 */
export function episodeRows(
  files: readonly ScannedRelease[],
  episodeCount: number | null | undefined,
  offset = 0,
): EpisodeRow[] {
  const byEpisode = new Map<number, ScannedRelease[]>();
  for (const file of files) {
    if (file.number === null) continue;
    // With an offset, the group's number is not this season's (build 112):
    // SubsPlease's 67 is episode 1, and its 39 to 66 are the seasons before
    // this one - files under the same name that this season does not have.
    const number = file.number - offset;
    if (number < 1) continue;
    byEpisode.set(number, [...(byEpisode.get(number) ?? []), file]);
  }
  const numbers = [...byEpisode.keys()].sort((a, b) => a - b);
  const first = numbers.length ? numbers[0] : 1;
  const last = Math.max(
    numbers.length ? numbers[numbers.length - 1] : 0,
    // Only extends the range when the numbering starts where the count does.
    // A group counting from the start of the franchise (51-62 against a stated
    // 12) must not produce fifty empty rows.
    first === 1 ? episodeCount ?? 0 : 0,
  );
  if (!numbers.length && !episodeCount) return [];
  const rows: EpisodeRow[] = [];
  for (let number = first; number <= last; number += 1) {
    rows.push({ number, files: byEpisode.get(number) ?? [] });
  }
  return rows;
}

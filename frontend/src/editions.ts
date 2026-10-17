/**
 * Splitting one group's files into the copies it publishes (build 98).
 *
 * The approach, as it was put: *first find all subgroups and quality and put
 * them in one list. If it can assign all episodes correctly, it is correct. If
 * it cannot, split it into smaller lists, moving the files out of the first
 * list - and then hopefully nothing is duplicated.*
 *
 * That is what this does, and it is a better rule than build 97's, which
 * found every tag that varied and split by all of them at once. Two things
 * were wrong with that:
 *
 * * it split groups that had nothing wrong with them. A group that changed
 *   codec halfway through a season has one file per episode and needs one
 *   row; build 97 tore it into two half-rows, neither of which was the season.
 * * it never checked that splitting had *worked*. The point of splitting is
 *   that each list holds one file per episode, and nothing measured that.
 *
 * So: **duplication is the question, and it is also the stopping condition.**
 * A list with one file per episode is finished. A list with two is split on
 * whichever tag removes the most duplication, and each half is asked again.
 * When no tag helps, what is left is genuine duplicates - two uploads of the
 * same thing, or a repack - and saying so is more useful than splitting on
 * noise.
 *
 * The rules each row ends up with are then read off the finished partition:
 * what all of its files carry that the group as a whole does not, and what the
 * other rows are recognised by. Those two lists are exactly the `edition` and
 * `edition_not` a profile stores, which is what makes the choice survive
 * adding - see backend/app/services/matching.py.
 *
 * The tags themselves come from the server, and deliberately include words
 * nobody has catalogued (services/release_edition.py), so a label a group
 * invents next season splits these lists without anything being added to a
 * list of known words.
 */

/** The fields these rules need. Anything with tags and an episode will do. */
export interface TaggedFile {
  /** The episode this file is, or null for a batch. */
  number: number | null;
  /** Every tag the server read out of the name. */
  tags: readonly string[];
}

/** One copy a group publishes, and how to recognise it. */
export interface Edition<T extends TaggedFile> {
  files: T[];
  /** Tags every file here carries and the group as a whole does not. */
  required: string[];
  /** Tags that identify the *other* copies, which none of these carry. */
  refused: string[];
}

/** A repack is the same copy re-uploaded, so it never splits a list. */
const NEVER_SPLITS = new Set(["repack"]);

/** How many distinct episodes a tag must appear on to be a real axis. */
const MIN_EPISODES = 2;

/** How many files each episode has, for the episodes that have more than one. */
export function duplicatesByEpisode(files: readonly TaggedFile[]): Map<number, number> {
  const counts = new Map<number, number>();
  for (const file of files) {
    if (file.number === null) continue;
    counts.set(file.number, (counts.get(file.number) ?? 0) + 1);
  }
  for (const [number, count] of [...counts]) {
    if (count < 2) counts.delete(number);
  }
  return counts;
}

/** How many files are in excess of one per episode - the thing to remove. */
function excess(files: readonly TaggedFile[]): number {
  let total = 0;
  for (const count of duplicatesByEpisode(files).values()) total += count - 1;
  return total;
}

function tagsOf(file: TaggedFile): Set<string> {
  return new Set([...file.tags].filter((tag) => !NEVER_SPLITS.has(tag)));
}

/** Tags carried by every one of these files. */
function commonTags(files: readonly TaggedFile[]): Set<string> {
  if (!files.length) return new Set();
  let common = tagsOf(files[0]);
  for (const file of files.slice(1)) {
    const here = tagsOf(file);
    common = new Set([...common].filter((tag) => here.has(tag)));
  }
  return common;
}

/**
 * Tags worth trying as a split: carried by some of these files but not all,
 * and seen on more than one episode.
 *
 * The episode rule is what keeps "END" on the finale, or a one-off note, from
 * looking like a choice. You cannot pick it for episode 3.
 */
function candidates(files: readonly TaggedFile[]): string[] {
  const episodesPerTag = new Map<string, Set<number>>();
  const filesPerTag = new Map<string, number>();
  const episodes = new Set<number>();
  for (const file of files) {
    if (file.number !== null) episodes.add(file.number);
    for (const tag of tagsOf(file)) {
      filesPerTag.set(tag, (filesPerTag.get(tag) ?? 0) + 1);
      if (file.number === null) continue;
      const seen = episodesPerTag.get(tag) ?? new Set<number>();
      seen.add(file.number);
      episodesPerTag.set(tag, seen);
    }
  }
  const found: string[] = [];
  for (const [tag, count] of filesPerTag) {
    if (count === files.length) continue; // every file has it: not a choice
    const spread = episodesPerTag.get(tag)?.size ?? 0;
    if (episodes.size > 1 && spread < MIN_EPISODES) continue;
    found.push(tag);
  }
  // Stable order, so two runs over the same files cannot disagree about which
  // tag to split on when several remove the same amount.
  return found.sort();
}

/**
 * Split until no list has two files for one episode.
 *
 * Recursive, and bounded by the number of tags: each split removes one tag
 * from consideration on both sides, and a split that removes no duplication is
 * not taken.
 */
function splitUntilUnique<T extends TaggedFile>(files: T[]): T[][] {
  if (!excess(files)) return [files];
  let best: { tag: string; withTag: T[]; without: T[]; left: number } | null = null;
  for (const tag of candidates(files)) {
    const withTag = files.filter((file) => tagsOf(file).has(tag));
    const without = files.filter((file) => !tagsOf(file).has(tag));
    if (!withTag.length || !without.length) continue;
    const left = excess(withTag) + excess(without);
    if (best === null || left < best.left) best = { tag, withTag, without, left };
  }
  // No tag removes any duplication: what is left is two uploads of the same
  // thing. Splitting on noise would turn a real finding into two half-rows.
  if (best === null || best.left >= excess(files)) return [files];
  return [...splitUntilUnique(best.withTag), ...splitUntilUnique(best.without)];
}

/**
 * One group's files as the copies it publishes, each with its rules.
 *
 * A group with one file per episode comes back as the single list it already
 * was, with no rules at all - which is the common case, and the one where
 * nothing should be asked.
 */
export function splitEditions<T extends TaggedFile>(files: T[]): Edition<T>[] {
  const parts = splitUntilUnique(files);
  if (parts.length < 2) {
    return [{ files, required: [], refused: [] }];
  }
  const groupWide = commonTags(files);
  const common = parts.map((part) => commonTags(part));
  // What each list is recognised by: what all of its files carry that the
  // group as a whole does not.
  const required = common.map((tags) =>
    [...tags].filter((tag) => !groupWide.has(tag)).sort(),
  );
  return parts.map((part, index) => {
    const mine = new Set<string>();
    for (const file of part) for (const tag of tagsOf(file)) mine.add(tag);
    // ...and what the *others* are recognised by, which none of these carry.
    // Both halves are needed: the plain copy carries everything the
    // dual-audio rule asks for minus one tag, so requiring alone would let
    // the dual-audio file into the plain row (reported against build 97).
    const refused = [
      ...new Set(
        required
          .filter((_, other) => other !== index)
          .flat()
          .filter((tag) => !mine.has(tag)),
      ),
    ].sort();
    return { files: part, required: required[index], refused };
  });
}

/** The rules as one comparable string, for a row key. */
export function editionKey(edition: { required: string[]; refused: string[] }): string {
  return [
    ...edition.required,
    ...edition.refused.map((tag) => `-${tag}`),
  ].join("+");
}

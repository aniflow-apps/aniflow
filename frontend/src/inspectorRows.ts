import type { InspectedRelease } from "./api/client";
import { editionKey, splitEditions, type TaggedFile } from "./editions";
import { normalizeGroup, qualityRank } from "./setupRows";

/**
 * The two panes of the Release Inspector (build 80).
 *
 * A debug page, and the debugging question is always the same: *the indexer
 * has this release, so why does AniFlow not show it?* Answering that needs
 * both halves side by side - everything that came back, and what survives
 * being grouped and filtered the way the add-anime step groups and filters.
 *
 * So the search returns every hit unfiltered and this module does the rest in
 * the browser. That is the point: changing a filter re-derives both panes from
 * the same data instead of running another search, so what you see excluded
 * really was excluded by the filter and not by a different set of results.
 */

/** Every field of the parse, as the API sends it. */
export interface ParsedFields {
  raw_name?: string;
  subgroup?: string | null;
  title?: string | null;
  episode?: string | null;
  version?: number;
  resolution?: string | null;
  media_source?: string | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  censored?: boolean | null;
  season?: number | null;
  crc32?: string | null;
  unknown_tags?: string[];
  /**
   * Everything the name says about itself that no other field accounted for.
   * Canonical where AniFlow knows the word, verbatim where it does not - see
   * services/release_edition.py. What *distinguishes* two copies is decided
   * from the whole set, in editions.ts.
   */
  tags?: string[];
}

/** One subgroup + quality combination, and the releases in it. */
export interface InspectorGroup {
  key: string;
  subgroup: string | null;
  resolution: string | null;
  title: string | null;
  /** What a library folder for these releases would be called. */
  folderName: string | null;
  releases: InspectedRelease[];
  /**
   * Which copy of each episode this row is - "uncensored", "dual-audio", the
   * codec - or empty when the group publishes one copy per episode.
   *
   * One group published three files per episode of the reported show, and
   * with one row for all of them it offered thirty-one files for a
   * twelve-episode season. Split, each row is twelve.
   */
  edition: string[];
  /**
   * What the *other* copies are recognised by, which none of this row's files
   * carry (build 98).
   *
   * Both halves are needed. The plain copy carries everything the dual-audio
   * row asks for minus one tag, so requiring alone let the dual-audio file
   * into the plain row - which is what "the second ToonsHub row does not
   * work" turned out to be.
   */
  editionNot: string[];
}

export interface InspectorFilters {
  /** Comma-separated group names, matched loosely. Empty means every group. */
  subgroup: string;
  /** One resolution, or "" for every quality. */
  resolution: string;
  /** Matched against what the parser recognised. See parsedTags. */
  tag: string;
}

/**
 * What the parser *recognised* in a name, as lowercase words.
 *
 * Only recognised fields on purpose: this filter answers "what did AniFlow
 * understand", which is a different question from "what does the name say"
 * and the more useful one when a release is being rejected. ``unknown_tags``
 * counts as recognised - the parser saw a tag there and could not classify
 * it, which is exactly the kind of thing worth filtering on ("multisub").
 */
export function parsedTags(parsed: ParsedFields | null | undefined): string[] {
  if (!parsed) return [];
  const tags: (string | null | undefined)[] = [
    parsed.media_source,
    parsed.video_codec,
    parsed.audio_codec,
    parsed.crc32,
    parsed.censored === null || parsed.censored === undefined
      ? null
      : parsed.censored ? "censored" : "uncensored",
    parsed.version && parsed.version > 1 ? `v${parsed.version}` : null,
    parsed.season !== null && parsed.season !== undefined ? `s${parsed.season}` : null,
    ...(parsed.unknown_tags ?? []),
  ];
  return tags.filter((tag): tag is string => Boolean(tag)).map((tag) => tag.toLowerCase());
}

/**
 * The tags about language that a row's files share - subtitles and dubs (build 239).
 *
 * *"This has a multisub, we should show it in the list too - interesting for people who want
 * something other than English."*  A tag every file of a group carries is not an *edition* (it tells
 * you nothing about which copy to take), so the row never listed it; but for these four it is exactly
 * what somebody reads the row for. Shown when at least half the row's files say it, and not when the
 * row is already told apart by it (the edition line shows that).
 */
export const LANGUAGE_TAGS = ["multi-subs", "english-sub", "dual-audio", "english-dub"] as const;

export function languageTagsOf(group: Pick<InspectorGroup, "releases" | "edition">): string[] {
  if (group.releases.length === 0) return [];
  const enough = Math.max(1, Math.ceil(group.releases.length / 2));
  return LANGUAGE_TAGS.filter((tag) => {
    if (group.edition.includes(tag)) return false;
    const carrying = group.releases.filter((release) => {
      const tags = (release.parsed as ParsedFields | null)?.tags ?? [];
      return tags.some((found) => found.toLowerCase() === tag);
    });
    return carrying.length >= enough;
  });
}

/** The single episodes a row's files cover, from the names alone. Batches and ranges count for none. */
export function episodeNumbersOf(group: Pick<InspectorGroup, "releases">): number[] {
  const numbers = new Set<number>();
  for (const release of group.releases) {
    const label = ((release.parsed as ParsedFields | null)?.episode ?? "").trim();
    if (/^\d+$/.test(label)) numbers.add(Number(label));
  }
  return [...numbers].sort((a, b) => a - b);
}

/** Another row of the same show that has episodes the chosen one lacks (the "other group" hint). */
export interface OtherGroupHint {
  subgroup: string | null;
  resolution: string | null;
  edition: string[];
  missing: number[];
}

/**
 * The other rows of this show holding episodes the chosen row does not, most first.
 *
 * Read from the search results already on screen, so it is a sample like the rest of the list - the
 * wording says "in these results". Rows of another folder are another show and never count.
 */
export function otherGroupHints(
  chosen: InspectorGroup,
  rows: InspectorGroup[],
  limit = 3,
): OtherGroupHint[] {
  const have = new Set(episodeNumbersOf(chosen));
  const show = chosen.folderName ?? chosen.title;
  return rows
    .filter((row) => row.key !== chosen.key && (row.folderName ?? row.title) === show)
    .map((row) => ({
      subgroup: row.subgroup,
      resolution: row.resolution,
      edition: row.edition,
      missing: episodeNumbersOf(row).filter((number) => !have.has(number)),
    }))
    .filter((hint) => hint.missing.length > 0)
    .sort((a, b) => b.missing.length - a.missing.length)
    .slice(0, limit);
}

/** What editions.ts needs out of a release: its episode and its tags. */
function tagged(release: InspectedRelease): TaggedFile {
  const parsed = release.parsed as ParsedFields | null;
  const label = (parsed?.episode ?? "").trim();
  return {
    // A batch or a range belongs to no single episode, which is what null
    // means to the rules in editions.ts.
    number: /^\d+$/.test(label) ? Number(label) : null,
    tags: parsed?.tags ?? [],
  };
}

/** Whether one release survives the filters. */
export function accepts(release: InspectedRelease, filters: InspectorFilters): boolean {
  const parsed = release.parsed as ParsedFields | null;
  const wanted = filters.subgroup
    .split(",")
    .map(normalizeGroup)
    .filter(Boolean);
  if (wanted.length && !wanted.includes(normalizeGroup(parsed?.subgroup))) return false;
  if (filters.resolution && (parsed?.resolution ?? "") !== filters.resolution) return false;
  const needle = filters.tag.trim().toLowerCase();
  if (needle && !parsedTags(parsed).some((tag) => tag.includes(needle))) return false;
  return true;
}

/**
 * The right-hand pane: one row per subgroup + quality, best quality first.
 *
 * Grouped the way the add-anime step groups, because that is what is being
 * debugged. A release the parser could not read has no group to belong to and
 * is left out of this side entirely - the left pane is where it shows up, with
 * the reason.
 */
export function groupReleases(
  releases: InspectedRelease[],
  filters: InspectorFilters,
  options: { splitByFolder?: boolean; splitByEdition?: boolean } = {},
): InspectorGroup[] {
  const groups = new Map<string, InspectorGroup>();
  for (const release of releases) {
    const parsed = release.parsed as ParsedFields | null;
    if (!parsed?.title) continue;
    if (!accepts(release, filters)) continue;
    const subgroup = parsed.subgroup ?? null;
    const resolution = parsed.resolution ?? null;
    const folderName = release.folder_name ?? null;
    // The inspector groups by group and quality alone: it is looking at one
    // show and an extra dimension would only fragment the view. The setup
    // step must split by folder as well - a search for "Youjo Senki" returns
    // both seasons, and picking a row there decides which show is being added.
    const key = options.splitByFolder
      ? `${folderName ?? parsed.title}|${normalizeGroup(subgroup)}|${resolution ?? ""}`
      : `${normalizeGroup(subgroup)}|${resolution ?? ""}`;
    const existing = groups.get(key);
    if (existing) {
      existing.releases.push(release);
      continue;
    }
    groups.set(key, {
      key,
      subgroup,
      resolution,
      title: parsed.title,
      folderName,
      releases: [release],
      edition: [],
      editionNot: [],
    });
  }
  const ordered = options.splitByEdition
    ? [...groups.values()].flatMap(byEdition)
    : [...groups.values()];
  return ordered.sort((a, b) =>
    qualityRank(a.resolution) - qualityRank(b.resolution)
    || (a.subgroup ?? "").localeCompare(b.subgroup ?? "")
    || a.edition.join("+").localeCompare(b.edition.join("+"))
  );
}

/**
 * One row per *copy* of an episode, where a group publishes several.
 *
 * Duplication is the question and the stopping condition: a list with one
 * file per episode is finished, and one with two is split until it is. See
 * editions.ts - a group that simply changed codec halfway through a season
 * has one file per episode and must stay one row.
 */
function byEdition(group: InspectorGroup): InspectorGroup[] {
  const withTags = group.releases.map((release) => ({
    release,
    ...tagged(release),
  }));
  const editions = splitEditions(withTags);
  if (editions.length < 2) return [group];
  return editions
    .map((edition) => ({
      ...group,
      key: `${group.key}|${editionKey(edition)}`,
      releases: edition.files.map((entry) => entry.release),
      edition: edition.required,
      editionNot: edition.refused,
    }))
    // Most files first: the copy a group puts most of its work into is
    // usually the one to take, and a better default than alphabetical.
    .sort((a, b) => b.releases.length - a.releases.length);
}

/** Every subgroup the search turned up, for the filter's own suggestions. */
export function subgroupsFound(releases: InspectedRelease[]): string[] {
  const names = new Set<string>();
  for (const release of releases) {
    const subgroup = (release.parsed as ParsedFields | null)?.subgroup;
    if (subgroup) names.add(subgroup);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

/** Every quality the search turned up, best first. */
export function resolutionsFound(releases: InspectedRelease[]): string[] {
  const found = new Set<string>();
  for (const release of releases) {
    const resolution = (release.parsed as ParsedFields | null)?.resolution;
    if (resolution) found.add(resolution);
  }
  return [...found].sort((a, b) => qualityRank(a) - qualityRank(b));
}

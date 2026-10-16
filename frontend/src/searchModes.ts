import type { InspectName } from "./api/client";

/**
 * How a search is put together, for every page that makes one (build 111).
 *
 * The Release Inspector is where these were worked out, one build at a time,
 * against the real tracker: what a release group actually types is not what a
 * catalogue publishes, and the difference between asking the right question
 * and the wrong one turned out to be 700 releases against 77. The measurements
 * that settled it are in the changelog for builds 107 to 110.
 *
 * They live here rather than in that page because the add step asks the same
 * question for real - *what does this show have?* - and had been asking it its
 * own way, worse. One list of modes, one set of rules, two pages.
 */

/** The names the latin-script modes work from. Japanese has its own mode. */
const LATIN_KINDS = new Set([
  "english", "romaji", "alternative", "anidb", "anilist", "mal", "kitsu",
  "shikimori", "clean",
]);

/** How many words the short form of a name keeps, by default. */
const SHORT_WORDS = 3;

/** How many words quick's own two searches keep (build 172). */
const QUICK_WORDS = 4;

/** How many pages the *quick* mode, and nothing else, reads per term. */
export const QUICK_PAGES = 1;

/**
 * Four modes, not eight (build 170; quick and full narrowed again in 172).
 *
 * The original eight were how the question got answered at all - measured one
 * at a time against the real tracker, builds 107 to 110 - but nobody outside
 * this file could tell "clean" from "cleanShort" from "titles" apart, and that
 * is the whole problem being fixed here: fewer names, each one obvious.
 *
 * - `quick` is English then Romaji (with the usual clean/anidb fallback),
 *   cleaned and cut to their first four words, one page each (172) - a
 *   release is found by its first few words or not at all, and a release
 *   group rarely gets past the fourth before the quality tag starts.
 * - `full` is `quick`'s own two searches plus the *whole* cleaned form of
 *   every name AniFlow holds, at no page limit - "make sure nothing was
 *   missed" without the exact-as-published spelling 170 also tried, which
 *   172 dropped as more noise than it was worth.
 * - `japanese` folds `quickJapanese` and `japanese` into one: the native
 *   title in the tracker's non-English category, the best Latin spelling
 *   beside it, no page limit.
 * - `custom` replaces `simple` and `qualities` both: the searcher's own list
 *   of terms, built by hand in the Release Inspector rather than guessed at
 *   here, with its own page depth.
 */
export type SearchMode = "quick" | "full" | "japanese" | "custom";

/** Every mode, quick first: it is the recommendation as well as the default. */
export const SEARCH_MODES: SearchMode[] = ["quick", "full", "japanese", "custom"];

/** What the add step starts with: two names, one page each, a few seconds. */
export const DEFAULT_MODE: SearchMode = "quick";

/** One search to run: a name, what to append to it, and which category. */
export interface SearchRun {
  base: string;
  extraTerms?: string[];
  native: boolean;
}

/**
 * How many pages of each term a mode reads.
 *
 * Undefined means every page the tracker has. `quick` stops at one: its terms
 * are already narrowed to four words, so a page's worth of the most-seeded
 * hits for that narrow a question is usually the whole answer. `custom` reads
 * whatever depth was set for it in the Release Inspector, or every page when
 * nothing was.
 */
export function pagesFor(mode: SearchMode, customPages?: number): number | undefined {
  if (mode === "quick") return QUICK_PAGES;
  if (mode === "custom") return customPages && customPages > 0 ? customPages : undefined;
  return undefined;
}

/**
 * The characters a search box is better off without.
 *
 * Asked for by example: *"Hetalia World★Stars (2026)"* should be searchable
 * as *"Hetalia World Stars (2026)"*. A release group writing a title into a
 * file name does not type a star, and a tracker matching on words does not
 * index one - so a title carrying one finds nothing under its own name.
 *
 * Each becomes a space rather than nothing, because they usually stand
 * *between* two words. The plain hyphen is in there because catalogues wrap a
 * subtitle in them - "Re:ZERO -Starting Life in Another World- Season 4".
 *
 * The named few first, then the blocks they come from - stars, hearts,
 * arrows, dingbats, emoji and the typographic spaces and dashes - so a title
 * with a symbol nobody has seen yet is cleaned as well.
 */
const SYMBOL_BLOCKS: [number, number][] = [
  [0x2000, 0x206f],   // the typographic spaces and dashes
  [0x2100, 0x2bff],   // symbols and dingbats: stars, hearts, arrows
  [0xfe00, 0xfe0f],   // the variation selectors that follow them
  [0x1f000, 0x1faff], // emoji
];
const SYMBOLS = new RegExp(
  "[:~_|-]|[" +
    SYMBOL_BLOCKS.map(([low, high]) =>
      String.fromCodePoint(low) + "-" + String.fromCodePoint(high)).join("") +
    "]",
  "gu",
);

/** One title with its symbols replaced by spaces, and the spaces tidied. */
export function clearSymbols(text: string): string {
  return text.replace(SYMBOLS, " ").replace(/\s+/g, " ").trim();
}

/**
 * A title with its season suffix taken off.
 *
 * The same question `services/release_identity.strip_season` answers on the
 * server, asked here because these terms are built in the browser. Kept
 * deliberately narrow - a suffix at the *end* and nothing else - since taking
 * a word off the middle of a name would change which show it is.
 */
export function stripSeason(value: string): string {
  const text = (value ?? "").trim();
  const patterns = [
    /\s+(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season)$/i,
    /\s+s\d{1,2}$/i,
    /\s+(?:part|cour)\s*\d+$/i,
    /\s+\(\d{4}\)$/,
  ];
  let out = text;
  for (const pattern of patterns) {
    const shorter = out.replace(pattern, "").trim();
    if (shorter) out = shorter;
  }
  return out || text;
}

/** One name as a search term: no season suffix, no symbols. */
export function searchable(value: string): string {
  return clearSymbols(stripSeason(value));
}

/** The first few words of a term, for the broadest question worth asking. */
export function firstWords(value: string, count = SHORT_WORDS): string {
  const words = value.split(/\s+/).filter(Boolean);
  return words.length <= count ? value : words.slice(0, count).join(" ");
}

/** The first name of one of these kinds, cleaned. */
function pick(names: InspectName[], kinds: string[]): string | null {
  for (const kind of kinds) {
    const found = names.find((name) => name.kind === kind);
    if (found) {
      const cleaned = searchable(found.value);
      if (cleaned.length > 1) return cleaned;
    }
  }
  return null;
}

/**
 * Quick's own two searches: English then Romaji (the usual clean/anidb
 * fallback), cleaned and cut to their first four words (build 172).
 *
 * `full` starts from this too, rather than repeating it - it is quick's
 * searches plus more, not a different idea.
 */
function quickRuns(names: InspectName[], query: string, native: boolean): SearchRun[] {
  const english = pick(names, ["english"]);
  const romaji = pick(names, ["romaji", "clean", "anidb"]);
  const runs = [english, romaji]
    .filter((base): base is string => Boolean(base))
    .map((base) => ({ base: firstWords(base, QUICK_WORDS), native: false }));
  return deduped(runs, firstWords(searchable(query), QUICK_WORDS), native);
}

/**
 * The runs one mode makes for one show.
 *
 * Kept out of both pages so the decision can be tested: what separates the
 * modes is how many requests they cost, which is the thing worth being sure
 * about before pressing a button that walks a tracker.
 *
 * `customTerms` is only read in `custom` mode - the list built by hand in the
 * Release Inspector's `+`/`-` list view. The box's own current text counts as
 * a term too, ahead of the list, since it is the one still being edited and
 * pressing Search should not require pressing `+` first.
 */
export function runsFor(
  mode: SearchMode,
  typed: string,
  names: InspectName[],
  native: boolean,
  customTerms: string[] = [],
): SearchRun[] {
  const query = typed.trim();

  if (mode === "custom") {
    const runs = [query, ...customTerms].map((base) => ({ base, native }));
    return deduped(runs, "", native);
  }

  if (mode === "quick") return quickRuns(names, query, native);

  if (mode === "japanese") {
    // The native title in the category the tracker keeps untranslated
    // releases in - for the shows an English-speaking group never touched -
    // and the best Latin spelling beside it, for the shows that got both.
    const japanese = pick(names, ["japanese"]);
    const romaji = pick(names, ["romaji", "clean", "anidb", "english"]);
    const runs: SearchRun[] = [];
    if (japanese) runs.push({ base: japanese, native: true });
    if (romaji) runs.push({ base: romaji, native: false });
    return deduped(runs, searchable(query), native);
  }

  // full: quick's own two searches, plus the *whole* cleaned form of every
  // name AniFlow holds - no page limit, for "make sure nothing was missed"
  // (build 172 dropped the exact-as-published spelling 170 also tried here:
  // more noise than it was worth). Japanese names are left out of the
  // cleaning: it is meant for the Latin spellings a catalogue mangles, and a
  // Japanese title still finds its own category through its `native` flag
  // either way.
  const quick = quickRuns(names, query, native);
  const cleanedNames = names
    .filter((name) => LATIN_KINDS.has(name.kind))
    .map((name) => searchable(name.value))
    .filter((name) => name.length > 1)
    .map((base) => ({ base, native: false }));
  const cleaned = deduped(cleanedNames, searchable(query), native);
  return deduped([...quick, ...cleaned], "", native);
}

/**
 * The runs with the repeats taken out, and the box as a fallback.
 *
 * Two names that clean to the same words are one question, and asking it
 * twice is a wasted walk through every page the tracker has. The category
 * counts as part of the question - the same words in the other category is a
 * different search.
 */
function deduped(runs: SearchRun[], fallback: string, native: boolean): SearchRun[] {
  const seen = new Set<string>();
  const kept: SearchRun[] = [];
  for (const run of runs) {
    const base = run.base.trim();
    if (base.length < 2) continue;
    const key = `${run.native ? "native:" : ""}${base.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    kept.push({ ...run, base });
  }
  if (kept.length > 0) return kept;
  return fallback.trim().length > 1 ? [{ base: fallback.trim(), native }] : [];
}

/**
 * The names a candidate carries, for a caller with no list of its own.
 *
 * The add step knows three spellings and the inspector asks the server for
 * every one AniFlow holds; this is the floor both can stand on.
 */
export function namesOfCandidate(candidate: {
  title_english?: string | null;
  title_romaji?: string | null;
  title_native?: string | null;
  display_name?: string;
}): InspectName[] {
  const found: InspectName[] = [];
  const offer = (kind: string, value: string | null | undefined, isNative = false) => {
    const text = (value ?? "").trim();
    if (!text || found.some((name) => name.value.toLowerCase() === text.toLowerCase())) return;
    found.push({ kind, value: text, native: isNative });
  };
  offer("english", candidate.title_english);
  offer("romaji", candidate.title_romaji);
  offer("japanese", candidate.title_native, true);
  if (found.length === 0) offer("clean", candidate.display_name);
  return found;
}

/**
 * Reading a calendar entry, apart from the page that draws it (build 101).
 *
 * The two things a card says beyond its name - how many people have it, and
 * how it relates to what is already tracked - are both judgements, and both
 * are easier to get right with a test than with a screenshot.
 */
import type { AnimeSearchResult } from "./api/client";
import { formatTime } from "./formatWhen";
import { has, t } from "./i18n";

/**
 * Every kind the sources have been seen to use (build 148).
 *
 * Mirrors `services/calendar_metadata.KNOWN_KINDS`. A kind outside this list
 * is still shown - the setting names what to *hide*, and `kindLabel` falls
 * back to the raw value - so the two lists drifting apart costs a checkbox
 * rather than a missing season.
 */
export const CALENDAR_KINDS = [
  "TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "TV_SPECIAL",
  "MUSIC", "CM", "PV",
] as const;

/**
 * What to call a kind on screen.
 *
 * Reported in build 148, twice over. *"It seems the text 'TV_SHORT' is not
 * localized"* - right: AniList's own spelling was printed verbatim, underscore
 * and all, because nothing had ever been written for it. And *"some animes
 * have 'movies', 'ona' 'tv' small lowercase"* - five sources, five
 * capitalisations, arriving on the same grid.
 *
 * So: normalise first, then look the kind up, and fall back to the normalised
 * value for anything nobody has written a word for. Falling back rather than
 * showing the key, because a kind a source invents next year should read as
 * itself rather than as `calendar.kind.WHATEVER`.
 */
export function kindLabel(format: string | null | undefined): string | null {
  if (!format) return null;
  const kind = format.trim().split(/\s+/).join("_").toUpperCase();
  if (!kind) return null;
  const key = `calendar.kind.${kind}`;
  return has(key) ? t(key) : kind.replace(/_/g, " ");
}

/**
 * What the calendar knows about where a show is (build 148).
 *
 * Mirrors `integrations/metadata/airing.AIRING_STATUSES`. `UNKNOWN` is a real
 * value and means nobody published one - it is never drawn.
 */
export const AIRING_STATUSES = [
  "NOT_YET_AIRED", "AIRING", "FINISHED", "CANCELLED", "UNKNOWN",
] as const;

/** What a status is called on screen, or null when it should not be drawn. */
export function statusLabel(status: string | null | undefined): string | null {
  if (!status || status === "UNKNOWN") return null;
  const key = `calendar.status.${status.trim().toUpperCase()}`;
  return has(key) ? t(key) : null;
}

/**
 * Which source is being asked, without saying which source it is (build 149).
 *
 * *"I am right now not in debug mode true ... I would like to avoid
 * displaying other sources by name."* Fair: which databases AniFlow talks to
 * is an implementation detail, and a progress line is a strange place to
 * publish it.
 *
 * So a letter, assigned by first sight and remembered for the run, which
 * keeps the line moving - "source A", then "source B" - without naming
 * anything. A debug installation still gets the real name, because working
 * out *which* source is slow is the entire point of that line there.
 *
 * The name is still in the API response; this hides it from the page rather
 * than from the network, which is what was asked for and is all a browser
 * can honestly promise anyway.
 */
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const seenSources = new Map<string, string>();

export function sourceAlias(source: string): string {
  const key = source.trim().toLowerCase();
  const known = seenSources.get(key);
  if (known) return known;
  const letter = LETTERS[seenSources.size % LETTERS.length];
  seenSources.set(key, letter);
  return letter;
}

/** What the progress line should say about the step in hand. */
export function stepText(step: string, debugMode = false): string {
  if (!step) return "";
  if (step === "artwork") return t("calendar.stepArtwork").replace("{count}", "");
  return debugMode
    ? t("calendar.stepSource").replace("{source}", step)
    : t("calendar.stepSourceAnonymous").replace("{letter}", sourceAlias(step));
}

/**
 * Popularity as one to five stars, relative to the season on screen.
 *
 * Relative, because the number itself means very little: it is a count of
 * how many people have the show on a list, added up across sources that count
 * differently, and a quiet season's best show would get one star against any
 * fixed threshold. Five stars means "one of the most-watched things airing",
 * which is the question somebody scanning a season is actually asking.
 *
 * The bands are by rank rather than by value so one runaway hit cannot
 * flatten everything below it: with a linear scale, a show with ten times the
 * count of every other would leave the whole season on one star.
 */
/** Genres that "hide adult content" is expected to cover beyond the flag.
 *
 * Asked for from the first real use of the filter: *"Hide adult content,
 * should also hide Ecchi"*. The provider's own `is_adult` is narrower than
 * what the switch is read to mean - it marks hentai and leaves fan-service
 * comedies in the grid - so the genre is checked as well.
 *
 * A list rather than one string because the next one will be Hentai or Erotica
 * under some provider's spelling, and matching is case-folded for the same
 * reason.
 */
const ADULT_GENRES = ["ecchi", "hentai", "erotica"];

/**
 * Whether the adult filter should hide this entry.
 *
 * One function so the grid and the "N hidden by your filters" line can never
 * disagree - they are two reads of the same question, and they used to be two
 * copies of `item.is_adult`.
 */
export function readsAsAdult(item: AnimeSearchResult): boolean {
  if (item.is_adult) return true;
  return (item.genres ?? []).some((genre) => ADULT_GENRES.includes(genre.trim().toLowerCase()));
}

/**
 * Whether the kind filter should leave this entry off the grid.
 *
 * One function, for the same reason `readsAsAdult` is one: the grid and the
 * "N hidden by your filters" line are two reads of the same question, and
 * they used to be two copies of it.
 *
 * Build 145. Until then v1 stripped MUSIC, CM and PV during the merge *and*
 * offered them as a setting - so the three kinds somebody could untick were
 * exactly the three unticking could not bring back. v2 stores every kind,
 * which leaves the setting as the only mechanism; this is what applies it.
 */
/** The season it is now, the same arithmetic the server uses.
 *
 * Computed rather than asked for so that a link straight to an old season
 * still knows where "Current season" goes. The server's answer replaces this
 * the first time it loads a season without being told which, so the two can
 * only differ in the closing hours of a quarter.
 */
export const SEASON_ORDER: readonly string[] = ["WINTER", "SPRING", "SUMMER", "FALL"];

export function thisSeason(): { year: number; season: string } {
  const now = new Date();
  return {
    year: now.getFullYear(),
    season: SEASON_ORDER[Math.floor(now.getMonth() / 3)],
  };
}

/**
 * The genres somebody typed, however they separated them.
 *
 * Commas, plusses or just spaces around a word - *"not sure how usually this
 * stuff is if you want to display 2 stuff, 'action, drama' or 'action +
 * drama'"*. Both, and either, because nobody should have to guess which
 * punctuation a filter box wants.
 */
export function parseGenres(raw: string): string[] {
  return raw
    .split(/[,+]/)
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Whether an entry has every genre asked for.
 *
 * *Every*, not any: typing "action, drama" means both, which is the narrowing
 * a second filter box is for. Matched as a prefix so "rom" finds Romance
 * while you are still typing.
 */
export function matchesGenres(item: AnimeSearchResult, wanted: readonly string[]): boolean {
  if (!wanted.length) return true;
  const has = (item.genres ?? []).map((genre) => genre.trim().toLowerCase());
  // Folded here as well as in `parseGenres`: a function that silently needs
  // its input pre-lowercased is a trap for the next caller.
  return wanted
    .map((want) => want.trim().toLowerCase())
    .every((want) => has.some((genre) => genre.startsWith(want)));
}

export function isHiddenKind(item: AnimeSearchResult, hidden: readonly string[]): boolean {
  if (!hidden.length) return false;
  const kind = (item.format ?? "").trim().toUpperCase();
  return kind !== "" && hidden.includes(kind);
}

export function starsIn(season: readonly AnimeSearchResult[]): (item: AnimeSearchResult) => number {
  const counts = season
    .map((item) => item.popularity ?? 0)
    .filter((count) => count > 0)
    .sort((a, b) => b - a);
  if (!counts.length) return () => 0;
  // The cut-off for each band: the top tenth is five stars, then a quarter,
  // then a half, then the rest.
  const at = (fraction: number) => counts[Math.min(counts.length - 1, Math.floor(counts.length * fraction))];
  const bands = [at(0.1), at(0.25), at(0.5), at(0.75)];
  return (item: AnimeSearchResult) => {
    const count = item.popularity ?? 0;
    if (count <= 0) return 0; // nothing said, rather than nothing watched
    if (count >= bands[0]) return 5;
    if (count >= bands[1]) return 4;
    if (count >= bands[2]) return 3;
    if (count >= bands[3]) return 2;
    return 1;
  };
}

/** "★★★☆☆" for three, and nothing at all when the count is unknown. */
export function starText(stars: number): string {
  return stars > 0 ? "★".repeat(stars) + "☆".repeat(5 - stars) : "";
}

/**
 * How far up the list a marked entry is lifted.
 *
 * Asked for as "it should be moved in popularity". A boost rather than a
 * section, because the calendar is read as one season and pulling four shows
 * out of it would break the thing it is for - and a gap (a season you appear
 * to have skipped) is worth more than a sequel, because nobody skips a middle
 * season on purpose.
 */
export function relatedBoost(related: string | null | undefined): number {
  if (related === "gap") return 2;
  if (related === "sequel") return 1;
  return 0;
}

/** Whether this entry carries an id Shoko and AniFlow can recognise it by. */
export function hasAnidbId(item: AnimeSearchResult): boolean {
  return Boolean(item.provider_ids?.anidb);
}

/**
 * The season's entries in the order the calendar shows them.
 *
 * The page's own sort first - airing date, name or popularity - and then the
 * marked ones lifted within it. Stable: two entries that tie keep the order
 * they arrived in, so the list does not reshuffle between renders.
 */
export function withRelatedFirst<T extends AnimeSearchResult>(items: readonly T[]): T[] {
  return [...items]
    .map((item, index) => ({ item, index }))
    .sort(
      (a, b) =>
        relatedBoost(b.item.related) - relatedBoost(a.item.related) || a.index - b.index,
    )
    .map((entry) => entry.item);
}

/** When it airs, as a weekday and a time, or null when nothing says. */
export function airsAt(item: AnimeSearchResult): { weekday: number; time: string } | null {
  if (!item.air_slot_at) return null;
  const when = new Date(item.air_slot_at);
  if (Number.isNaN(when.getTime())) return null;
  return { weekday: when.getDay(), time: formatTime(when) };
}

/**
 * The weekday a show airs on, whether or not anybody published a time.
 *
 * Reported as an inconsistency: an entry with only a premiere date showed
 * `Airs 7/3/2026` and no premiere row, while an entry with a slot showed
 * `Airs Fri · 04:30 PM` and the premiere underneath. The weekday is in the
 * premiere date either way — the 3rd of July 2026 is a Friday whether or not
 * AniList also said 16:30 — so the answer is the same shape for both, with
 * the time added when there is one.
 */
export function airsOn(item: AnimeSearchResult): { weekday: number; time: string | null } | null {
  const slot = airsAt(item);
  if (slot) return slot;
  if (!item.air_start_date) return null;
  const when = new Date(item.air_start_date);
  if (Number.isNaN(when.getTime())) return null;
  return { weekday: when.getDay(), time: null };
}

/**
 * The kind in as few letters as it takes (build 210), for a card too narrow for the whole word:
 * "Movie" is "Mov.", "Special" is "Spec.". Short ones (TV, OVA, ONA) are kept whole.
 */
export function kindShort(format: string | null | undefined): string | null {
  const full = kindLabel(format);
  if (!full) return null;
  if (full.length <= 4) return full;
  // "TV short" and "TV-Special" are TV first of all.
  const first = full.split(/[\s-]+/)[0];
  if (first.length < full.length && first.length <= 4) return first;
  return `${full.slice(0, 3)}.`;
}

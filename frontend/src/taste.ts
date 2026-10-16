/**
 * What the watchlist says the user likes, and how a season measures up (build 208).
 *
 * One tally - the genres of what is on the watchlist - feeds the taste window,
 * the calendar's "Recommended" order and, later, the suggestions in Add anime.
 * Pure and explainable on purpose: no learning, no hidden state. A show scores
 * high when its genres are the ones the user collects most, nudged by how many
 * people list it.
 */
import type { AnimeSearchResult, AnimeSummary } from "./api/client";
import { isHiddenKind, readsAsAdult } from "./calendarView";
import { tracksAlready } from "./library";
import { genreCounts } from "./watchlistLibraries";

/** How much the genres count against popularity. A taste match leads; popularity breaks near-ties. */
export const TASTE_WEIGHT = 0.65;

/** How many of a show's genres are averaged: a show tagged with seven genres is not seven times a match. */
const GENRES_USED = 3;

/** genre (lower case) -> share of the most-collected genre, 0..1. Empty when the watchlist has no genres. */
export type TasteProfile = Map<string, number>;

export function tasteProfile(watchlist: readonly AnimeSummary[]): TasteProfile {
  const counts = genreCounts([...watchlist]);
  const profile: TasteProfile = new Map();
  const top = counts[0]?.count ?? 0;
  if (top === 0) return profile;
  for (const { genre, count } of counts) profile.set(genre.toLowerCase(), count / top);
  return profile;
}

/** How well a show's genres match the profile, 0..1: the mean of its best few genres' weights. */
export function affinity(genres: readonly string[], profile: TasteProfile): number {
  if (profile.size === 0 || genres.length === 0) return 0;
  const weights = genres
    .map((genre) => profile.get(genre.trim().toLowerCase()) ?? 0)
    .sort((a, b) => b - a)
    .slice(0, GENRES_USED);
  return weights.reduce((sum, weight) => sum + weight, 0) / weights.length;
}

/** Where a show stands in the season by list counts, 0..1 (1 = the most listed). 0 when nothing is known. */
export function popularityRank(season: readonly AnimeSearchResult[]): (item: AnimeSearchResult) => number {
  const counts = season
    .map((item) => item.popularity ?? 0)
    .filter((count) => count > 0)
    .sort((a, b) => b - a);
  if (counts.length === 0) return () => 0;
  return (item) => {
    const count = item.popularity ?? 0;
    if (count <= 0) return 0;
    const better = counts.findIndex((other) => other <= count);
    return 1 - better / counts.length;
  };
}

/**
 * The season's entries, best match first: taste plus popularity. Stable - a tie keeps the order the
 * season arrived in, which is most-listed first. With no taste to go on (an empty watchlist or no
 * genres stored) the order is simply popularity.
 */
export function sortByRecommendation<T extends AnimeSearchResult>(
  items: readonly T[],
  season: readonly AnimeSearchResult[],
  profile: TasteProfile,
): T[] {
  const rank = popularityRank(season);
  const score = (item: T) => TASTE_WEIGHT * affinity(item.genres ?? [], profile) + (1 - TASTE_WEIGHT) * rank(item);
  return items
    .map((item, index) => ({ item, index, score: score(item) }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((entry) => entry.item);
}

/** The kinds left out of suggestions: a music video or a commercial is not something to watch next. */
const NOT_SUGGESTED_KINDS = ["MUSIC", "CM", "PV"];

/**
 * What to suggest in Add anime (build 208): this season's best matches for the watchlist's taste, minus
 * what is already tracked, adult shows and non-episodic kinds. Without any taste it is the most
 * popular - the season arrives in that order. Never more than `count`.
 */
export function suggestionsFor(
  season: readonly AnimeSearchResult[],
  watchlist: readonly AnimeSummary[],
  count = 5,
): AnimeSearchResult[] {
  const candidates = season.filter(
    (item) =>
      !item.in_library
      && !tracksAlready([...watchlist], item)
      && !readsAsAdult(item)
      && !isHiddenKind(item, NOT_SUGGESTED_KINDS)
      && item.addable !== false,
  );
  return sortByRecommendation(candidates, season, tasteProfile(watchlist)).slice(0, count);
}

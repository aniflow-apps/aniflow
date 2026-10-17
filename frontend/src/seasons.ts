/**
 * Which season an anime belongs to, and whether that is the current one.
 *
 * Build 100, asked for since build 94: split the watchlist into the season
 * airing now, the series only Shoko has, and everything else - which in
 * practice is last season's and the ones before it. With a dozen entries the
 * single grid was fine; the request was made with a hundred in mind, and by
 * then "what is on this week" is the only part anybody looks at.
 *
 * The season comes from the first airing date, which AniFlow already stores
 * for everything it added from a provider. An anime with no date at all is
 * *not* guessed at - it goes in the same place a finished show goes, because
 * the alternative is putting somebody's oldest entry at the top of their
 * current season.
 */

/** The four seasons, as the calendar already names them. */
export type SeasonName = "WINTER" | "SPRING" | "SUMMER" | "FALL";

export interface Season {
  year: number;
  season: SeasonName;
}

/**
 * Which season a month belongs to: three months each, from January.
 *
 * Winter is January to March, spring April to June, summer July to September,
 * autumn October to December - which is the convention every anime catalogue
 * uses, and the one `services/calendar.resolve_season` has always used on the
 * server. Build 100 invented different boundaries here and put September in
 * autumn, so the watchlist announced a season the calendar disagreed with.
 *
 * Local time rather than UTC, for the same reason the server uses
 * `date.today()`: the season is a fact about the viewer's calendar.
 */
export function seasonOf(date: Date): Season {
  const order: SeasonName[] = ["WINTER", "SPRING", "SUMMER", "FALL"];
  return { year: date.getFullYear(), season: order[Math.floor(date.getMonth() / 3)] };
}

/** The season now, by the viewer's clock. */
export function currentSeason(now: Date = new Date()): Season {
  return seasonOf(now);
}

export function sameSeason(one: Season | null, other: Season | null): boolean {
  return Boolean(one && other && one.year === other.year && one.season === other.season);
}

/** The season an anime started in, or null when nothing says. */
export function seasonOfAnime(airStartDate: string | null | undefined): Season | null {
  if (!airStartDate) return null;
  // "2026-01-09" and full timestamps both; an unparseable value is a value to
  // ignore rather than to guess from.
  const parsed = new Date(airStartDate.length <= 10 ? `${airStartDate}T00:00:00Z` : airStartDate);
  return Number.isNaN(parsed.getTime()) ? null : seasonOf(parsed);
}

/**
 * The season an anime belongs to (build 190): the one the providers place it
 * in when AniFlow knows it, otherwise the one its premiere date means.
 *
 * Two things the date alone gets wrong. A show that premieres in the last days
 * of a season - 26 September, say - is a fall anime by every catalogue, so the
 * fallback allows a week at the end of a season (`EDGE_DAYS`, the same as the
 * server's). And a show added months ahead has a date that is still to come
 * but a season that is already known, which is what lets it be filed under
 * the season it is part of before its first episode has aired.
 */
export const EDGE_DAYS = 7;

export function seasonFor(anime: {
  season?: string | null;
  season_year?: number | null;
  air_start_date?: string | null;
}): Season | null {
  const name = (anime.season ?? "").toUpperCase();
  if (anime.season_year && (name === "WINTER" || name === "SPRING" || name === "SUMMER" || name === "FALL")) {
    return { year: anime.season_year, season: name };
  }
  if (!anime.air_start_date) return null;
  const parsed = new Date(anime.air_start_date.length <= 10 ? `${anime.air_start_date}T00:00:00Z` : anime.air_start_date);
  if (Number.isNaN(parsed.getTime())) return null;
  return seasonOf(new Date(parsed.getTime() + EDGE_DAYS * 86400000));
}

/**
 * Whether every episode the show has is already on disk (build 188).
 *
 * The count must be known: a show whose total nobody has stated is not
 * "complete" at zero or at one, it is unknown, and unknown stays where it is.
 */
export function allEpisodesOwned(anime: { owned_episodes?: number; episode_count?: number | null }): boolean {
  const total = anime.episode_count ?? 0;
  return total > 0 && (anime.owned_episodes ?? 0) >= total;
}

/**
 * Whether this anime belongs in the "airing now" section.
 *
 * A show that started *last* season and is still running counts: a
 * two-cour series is the most-watched thing in anybody's list in its second
 * half, and filing it under "earlier" would be obviously wrong. So the test
 * is the season it started in being this one or the one before, *and* it not
 * being finished.
 *
 * "Finished" is not the lifecycle alone (build 188). Reported in October with
 * most of last season's twelve-episode shows still in the airing section at
 * 12 / 12: nothing had moved their lifecycle to COMPLETE, so the record said
 * they were running while the library said every episode was there. A show
 * from the previous season with every episode on disk is therefore finished,
 * whatever the record says - the same stance as `notYetAiring`, where the
 * date overrides a provider that updates late. A show that is 12 / 13 or
 * 14 / 26 is still running and stays.
 */
export function airingNow(
  anime: {
    air_start_date?: string | null;
    season?: string | null;
    season_year?: number | null;
    lifecycle?: string;
    owned_episodes?: number;
    episode_count?: number | null;
  },
  now: Date = new Date(),
): boolean {
  if (anime.lifecycle && anime.lifecycle !== "ACTIVE" && anime.lifecycle !== "UPCOMING") {
    return false;
  }
  // The provider's season when there is one, else the date (build 190). A
  // show of this season counts even before its first episode has aired.
  const started = seasonFor(anime);
  if (started === null) return false;
  const current = currentSeason(now);
  if (sameSeason(started, current)) return true;
  return sameSeason(started, previousSeason(current)) && !allEpisodesOwned(anime);
}

/**
 * Whether this anime has not started yet (build 117).
 *
 * Its own small group on the watchlist, between what is airing and what has
 * finished. Asked for after the two-category rework: an announced show is
 * neither of those things, and putting it with the finished ones reads as a
 * mistake while putting it with the airing ones quietly claims episodes
 * exist.
 *
 * The date decides when there is one, because a provider's lifecycle is
 * updated late and often - a show that started on Tuesday is airing whatever
 * the record says. With no date at all, only the lifecycle is left.
 */
export function notYetAiring(
  anime: { air_start_date?: string | null; lifecycle?: string },
  now: Date = new Date(),
): boolean {
  if (anime.lifecycle && anime.lifecycle !== "ACTIVE" && anime.lifecycle !== "UPCOMING") {
    return false;
  }
  if (anime.air_start_date) {
    const starts = new Date(anime.air_start_date);
    if (!Number.isNaN(starts.getTime())) return starts.getTime() > now.getTime();
    return false;
  }
  return anime.lifecycle === "UPCOMING";
}

/** The season before this one. */
export function previousSeason(season: Season): Season {
  const order: SeasonName[] = ["WINTER", "SPRING", "SUMMER", "FALL"];
  const index = order.indexOf(season.season);
  if (index === 0) return { year: season.year - 1, season: "FALL" };
  return { year: season.year, season: order[index - 1] };
}

/** "Winter 2026", for a heading. */
export function seasonLabel(season: Season): string {
  const names: Record<SeasonName, string> = {
    WINTER: "Winter",
    SPRING: "Spring",
    SUMMER: "Summer",
    FALL: "Fall",
  };
  return `${names[season.season]} ${season.year}`;
}

/** Whether `season` comes after `other`. */
export function seasonIsAfter(season: Season, other: Season): boolean {
  const order: SeasonName[] = ["WINTER", "SPRING", "SUMMER", "FALL"];
  return season.year * 4 + order.indexOf(season.season) > other.year * 4 + order.indexOf(other.season);
}

/** The first day of a season, as the calendar's own date string. */
export function seasonStartDate(season: Season): string {
  const order: SeasonName[] = ["WINTER", "SPRING", "SUMMER", "FALL"];
  const month = String(order.indexOf(season.season) * 3 + 1).padStart(2, "0");
  return `${season.year}-${month}-01`;
}

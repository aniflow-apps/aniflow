/**
 * The release date an episode row shows (build 216).
 *
 * The server sends the day and how it knows it: a date somebody stored for that episode (`recorded`), the
 * premiere itself (`exact`), or the premiere counted forward a week per episode (`from_premiere`). The last is
 * an estimate and the row says so - a broadcast break or a recap week makes it wrong, and a person reading
 * "Saturday 17.10.2026" should know nobody published that.
 */
import type { EpisodeAirDate } from "./api/client";
import { t } from "./i18n";
import { formatDateOnly } from "./time";

export interface AirLine {
  /** Out already (today counts: the day has come). */
  released: boolean;
  estimated: boolean;
  /** "Saturday 26.09.2026", in the app's language and the reader's date order. */
  when: string;
  /** The sentence for the row, from the locale. */
  text: string;
}

/** "Saturday 26.09.2026" from a plain `YYYY-MM-DD`: time.ts's weekday and date, without its comma. */
export function weekdayAndDate(day: string): string {
  return formatDateOnly(day).replace(", ", " ");
}

function isoDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** What the row says about when this episode came out or is due; null when nothing is known. */
export function airLine(airDate: EpisodeAirDate | null | undefined, today: Date = new Date()): AirLine | null {
  if (!airDate?.on) return null;
  const estimated = airDate.basis !== "recorded" && airDate.basis !== "exact";
  const released = airDate.on.slice(0, 10) <= isoDay(today);
  const when = weekdayAndDate(airDate.on);
  const key = released
    ? (estimated ? "episode.airedAround" : "episode.airedOn")
    : (estimated ? "episode.dueAround" : "episode.dueOn");
  return { released, estimated, when, text: t(key).replace("{date}", when) };
}

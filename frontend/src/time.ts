/**
 * How AniFlow writes a timestamp - in the order the reader chose (build 190).
 *
 * This file used to hand the date to the browser's own locale
 * (`toLocaleString(undefined, ...)`), so a machine set to US English wrote
 * "Oct 2, 11:47 AM" on the Activity, Attention, In Progress and RSS pages
 * whatever the Settings said - while the Settings page itself, and the
 * calendar, wrote what was asked for. Reported as "I have the European date
 * in Settings and this shows the US one". Everything here now goes through
 * `formatWhen.ts`, which is the one place the choice is read.
 *
 * The functions keep their names and their empty-string answer for "no value",
 * because the callers read `formatTime(x) || "never"`.
 *
 * The value is an ISO instant from the API, which carries its UTC offset, so
 * the browser converts it to wherever the viewer is. A timestamp without an
 * offset would be read as local time and silently shown hours out.
 */
import { formatDate, formatDateTime, formatDateTimeFull, formatTime as clockTime } from "./formatWhen";
import { getLanguage } from "./i18n";

const EMPTY = "";

/** "02.10.2026 11:47" - for lists. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return formatDateTime(value);
}

/** "02.10.2026 11:47:03" - for details, where precision is the point. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return formatDateTimeFull(value);
}

/**
 * "Monday, 01:30" - when an episode airs, week after week (build 85).
 *
 * The weekday matters more than the date: a calendar entry answers "which
 * evening is this on", and the exact instant of the first episode is a
 * detail. The weekday is in the app's language, the time in the reader's
 * chosen format, and both in their own timezone - which is the whole reason
 * the backend stores one real instant in UTC rather than a weekday and a clock
 * time.
 */
export function formatAirSlot(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return EMPTY;
  const weekday = when.toLocaleDateString(getLanguage(), { weekday: "long" });
  return `${weekday}, ${clockTime(when)}`;
}

/**
 * "Monday, 06.07.2026" from a plain date.
 *
 * Built from the parts rather than passed to `new Date`, which reads a bare
 * "2026-07-06" as midnight UTC and so shows the previous day to anybody west
 * of Greenwich.
 */
export function formatDateOnly(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const parts = value.slice(0, 10).split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return value;
  const when = new Date(parts[0], parts[1] - 1, parts[2]);
  const weekday = when.toLocaleDateString(getLanguage(), { weekday: "long" });
  return `${weekday}, ${formatDate(value.slice(0, 10))}`;
}

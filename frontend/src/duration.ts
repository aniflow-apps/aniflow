/**
 * Shorthand durations for the Schedule page and its Settings controls
 * (build 158): `5m`, `1h`, `1d`, `1w`, `1M`. Lower-case `m` is minutes,
 * capital `M` is a month - the one ambiguity the shorthand has to resolve.
 *
 * Storage and the API stay plain integer seconds throughout, matching every
 * interval AniFlow already had before this - this module only exists at the
 * edges, for typing one in and reading one back. `1M` is a fixed 30 days, not
 * a calendar month: every task on this page is interval-based (elapsed time
 * since the last run), never tied to a wall-clock date.
 */
import { getLanguage } from "./i18n";

const UNIT_SECONDS: Record<string, number> = {
  s: 1,
  m: 60,
  h: 3600,
  d: 86400,
  w: 604800,
  M: 2592000, // 30 days
};

/** Largest unit first, so formatting picks the roundest label. */
const UNITS_LARGEST_FIRST: [string, number][] = [
  ["M", 2592000],
  ["w", 604800],
  ["d", 86400],
  ["h", 3600],
  ["m", 60],
  ["s", 1],
];

/** "5m" / "1h" / "1d" / "1w" / "1M", or a bare number of seconds. */
export function parseDuration(text: string): number | null {
  const trimmed = text.trim();
  const match = /^(\d+(?:\.\d+)?)\s*(s|m|h|d|w|M)?$/.exec(trimmed);
  if (!match) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
  const unit = match[2] ?? "s";
  return Math.round(value * UNIT_SECONDS[unit]);
}

/** The shorthand form of a stored interval, for an editable field: "1h". */
export function formatDurationShorthand(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "";
  for (const [unit, size] of UNITS_LARGEST_FIRST) {
    if (totalSeconds >= size && totalSeconds % size === 0) {
      return `${totalSeconds / size}${unit}`;
    }
  }
  return `${totalSeconds}s`;
}

/** The units the Schedule page's own editor offers (build 161) - never a
 *  bare number of seconds, since nothing schedulable runs that often. */
export const EDITABLE_UNITS: readonly string[] = ["m", "h", "d", "w", "M"];

/** A stored interval, split into what the editor's two controls show:
 *  a number and one of `EDITABLE_UNITS`. Picks the largest unit that divides
 *  evenly, same rule as `formatDurationShorthand` - so 21600 becomes
 *  `{ value: 6, unit: "h" }`, not `{ value: 21600, unit: "s" }`. Falls back to
 *  whole minutes, rounded, for a value nothing above evenly divides - every
 *  interval field's own floor is at least a minute, so this only ever loses
 *  a fraction smaller than the editor could enter anyway. */
export function decomposeSeconds(totalSeconds: number): { value: number; unit: string } {
  for (const [unit, size] of UNITS_LARGEST_FIRST) {
    if (unit === "s") continue;
    if (totalSeconds >= size && totalSeconds % size === 0) {
      return { value: totalSeconds / size, unit };
    }
  }
  return { value: Math.max(1, Math.round(totalSeconds / 60)), unit: "m" };
}

/** The inverse of `decomposeSeconds` - what the editor sends back. */
export function composeSeconds(value: number, unit: string): number {
  return Math.round(value * (UNIT_SECONDS[unit] ?? 60));
}

const SPELLED_OUT: Record<string, [string, string]> = {
  M: ["month", "months"],
  w: ["week", "weeks"],
  d: ["day", "days"],
  h: ["hour", "hours"],
  m: ["minute", "minutes"],
  s: ["second", "seconds"],
};

/** "6 hours" / "7 minutes" / "1 day" - read-only, for the Schedule page. */
export function formatDurationWords(totalSeconds: number | null): string {
  if (totalSeconds === null || !Number.isFinite(totalSeconds) || totalSeconds <= 0) return "";
  for (const [unit, size] of UNITS_LARGEST_FIRST) {
    if (totalSeconds >= size && totalSeconds % size === 0) {
      const value = totalSeconds / size;
      const [singular, plural] = SPELLED_OUT[unit];
      return `${value} ${value === 1 ? singular : plural}`;
    }
  }
  return `${totalSeconds} seconds`;
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["week", 604800],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

/** "an hour ago" / "in 5 hours" / "now" - Last/Next Execution, Sonarr-style. */
export function formatRelative(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "";
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return "";
  const diffSeconds = Math.round((when.getTime() - now.getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(getLanguage(), { numeric: "auto" });
  for (const [unit, size] of RELATIVE_UNITS) {
    if (Math.abs(diffSeconds) >= size) {
      return rtf.format(Math.round(diffSeconds / size), unit);
    }
  }
  return rtf.format(0, "second");
}

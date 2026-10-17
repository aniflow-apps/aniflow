/**
 * Writing dates and times the way the reader asked for (build 146).
 *
 *     *"I noticed the date and time is US, I do not like this lets change it
 *     to Europe, maybe add a Settings in General [...] For time give the
 *     options AM/PM or 24 hours - make 24 hours default. For Date, have
 *     DAY/MON/YEAR (default), MON/DAY/YEAR and YEAR/MON/DAY as option."*
 *
 * The browser's own locale was deciding, which on a machine set to US English
 * gives `04:30 PM` and `7/3/2026` however European the person reading it is.
 * The machine's locale is a fact about the machine; this is a preference about
 * the page, and they are not the same question.
 *
 * Formatted by hand rather than through `toLocaleDateString`, because the
 * point is a fixed order the reader chose - passing a locale that happens to
 * produce day-first would be asking for the same answer sideways, and would
 * change again on the next machine.
 */
export type TimeFormat = "24h" | "12h";
export type DateFormat = "dmy" | "mdy" | "ymd";

/** What is used until the settings have been read, and if they never are. */
export const DEFAULT_TIME: TimeFormat = "24h";
export const DEFAULT_DATE: DateFormat = "dmy";

let timeFormat: TimeFormat = DEFAULT_TIME;
let dateFormat: DateFormat = DEFAULT_DATE;

/**
 * Remember what the reader asked for.
 *
 * Module state rather than a context: every view that shows a date would
 * otherwise have to thread it down, and the answer is the same everywhere on
 * the page. Set once, when the settings arrive.
 *
 * Named `set`, not `use`: it is not a hook, and a `use` prefix on something
 * that is not one misleads a reader and eventually trips the hooks lint.
 */
export function setWhenFormats(time?: string | null, date?: string | null): void {
  if (time === "24h" || time === "12h") timeFormat = time;
  if (date === "dmy" || date === "mdy" || date === "ymd") dateFormat = date;
}

export function whenFormats(): { time: TimeFormat; date: DateFormat } {
  return { time: timeFormat, date: dateFormat };
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}

/** A calendar date with no time ("2026-09-19"), as a local date. */
const PLAIN_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Built from the parts, never through `new Date("2026-09-19")` (build 190),
 * which reads a bare date as midnight UTC and so shows the day before to
 * anybody west of Greenwich. A premiere date is a day, not an instant.
 */
export function plainDate(value: string): Date | null {
  const found = PLAIN_DATE.exec(value.trim());
  if (!found) return null;
  const when = new Date(Number(found[1]), Number(found[2]) - 1, Number(found[3]));
  return Number.isNaN(when.getTime()) ? null : when;
}

/** `19.09.2026`, `09/19/2026` or `2026-09-19`, per the setting. */
export function formatDate(value: Date | string | null | undefined): string {
  if (!value) return "—";
  const when = value instanceof Date ? value : (typeof value === "string" && plainDate(value)) || new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  const day = two(when.getDate());
  const month = two(when.getMonth() + 1);
  const year = String(when.getFullYear());
  if (dateFormat === "mdy") return `${month}/${day}/${year}`;
  if (dateFormat === "ymd") return `${year}-${month}-${day}`;
  // Dots for day-first, which is how it is written where day-first is used,
  // and which also stops a reader having to guess whether 09/19 is a date in
  // September or a nineteenth month.
  return `${day}.${month}.${year}`;
}

/** `16:30` or `04:30 PM`, per the setting. */
export function formatTime(value: Date | string | null | undefined, seconds = false): string {
  if (!value) return "—";
  const when = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  return clock(when.getHours(), when.getMinutes(), seconds ? when.getSeconds() : null);
}

function clock(hours: number, minutes: number, seconds: number | null): string {
  const tail = `${two(minutes)}${seconds === null ? "" : `:${two(seconds)}`}`;
  if (timeFormat === "12h") {
    const suffix = hours < 12 ? "AM" : "PM";
    const twelve = hours % 12 === 0 ? 12 : hours % 12;
    return `${two(twelve)}:${tail} ${suffix}`;
  }
  return `${two(hours)}:${tail}`;
}

/**
 * A time of day the server wrote as "16:00" (build 190): when a show airs.
 * Written the way the reader asked for, like every other time on the page; the
 * value itself is untouched, only how it is shown.
 */
export function formatClock(value: string | null | undefined): string {
  const found = /^(\d{1,2}):(\d{2})/.exec(value ?? "");
  if (!found) return value ?? "";
  return clock(Number(found[1]), Number(found[2]), null);
}

/** The hint inside a date box: how to type a date the way the setting writes it. */
export function dateInputHint(): string {
  if (dateFormat === "mdy") return "MM/DD/YYYY";
  if (dateFormat === "ymd") return "YYYY-MM-DD";
  return "DD.MM.YYYY";
}

/**
 * What somebody typed into a date box, as "YYYY-MM-DD" - or null when it is not
 * a date (build 190). The order is the setting's, so `07.03.2026` is the
 * seventh of March for day-first and the third of July for month-first, which
 * is the point; `/`, `.`, `-` and spaces all separate. A real calendar day
 * only: 31.02.2026 is refused rather than rolled into March.
 */
export function parseDateInput(text: string): string | null {
  const parts = text.trim().split(/[./\-\s]+/).filter(Boolean);
  if (parts.length !== 3 || parts.some((part) => !/^\d+$/.test(part))) return null;
  let year: number;
  let month: number;
  let day: number;
  if (dateFormat === "ymd") [year, month, day] = parts.map(Number);
  else if (dateFormat === "mdy") [month, day, year] = parts.map(Number);
  else [day, month, year] = parts.map(Number);
  if (year < 1900 || year > 2200) return null;
  const when = new Date(year, month - 1, day);
  if (when.getFullYear() !== year || when.getMonth() !== month - 1 || when.getDate() !== day) return null;
  return `${year}-${two(month)}-${two(day)}`;
}

/** Both, with seconds, for a detail where precision is the point (build 190). */
export function formatDateTimeFull(value: Date | string | null | undefined): string {
  if (!value) return "—";
  const when = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  return `${formatDate(when)} ${formatTime(when, true)}`;
}

/** Both, for a log line or a trace: `19.09.2026 16:30`. */
export function formatDateTime(value: Date | string | null | undefined): string {
  if (!value) return "—";
  const when = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  return `${formatDate(when)} ${formatTime(when)}`;
}

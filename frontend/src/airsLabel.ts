import { formatDate, plainDate, whenFormats } from "./formatWhen";
import { t } from "./i18n";

export interface AirsLabel {
  /** On the card: "in 5 days", "tomorrow", or the date in the user's own format. */
  text: string;
  /** The hover: always the full date. */
  title: string;
  /** Whole days from today; at least 1. */
  days: number;
}

/** Up to this many days away the card counts down instead of giving the date. */
export const COUNTDOWN_DAYS = 7;

/**
 * What a card says about an anime whose first episode is still ahead (build 206), or null.
 *
 * "in 5 days", then "in 4 days" ... "tomorrow", and a week or more out the
 * date as Settings writes dates. Nothing for a show that has started or has
 * no date: a "date unknown" pill would be noise. The day is a calendar day,
 * not 24 hours: a show on the 7th is "tomorrow" all of the 6th.
 */
export function airsLabel(date: string | null | undefined, now: Date = new Date()): AirsLabel | null {
  if (!date) return null;
  const starts = plainDate(date);
  if (starts === null) return null;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((starts.getTime() - today.getTime()) / 86_400_000);
  if (days < 1) return null;
  const full = formatDate(starts);
  const text =
    days === 1 ? t("airs.tomorrow") : days <= COUNTDOWN_DAYS ? t("airs.inDays").replace("{n}", String(days)) : full;
  return { text, title: t("airs.title").replace("{date}", full), days };
}

/**
 * The date bubble on a calendar card (build 210): the premiere as Settings writes dates, and what is
 * known of it when it is not whole - "?.12.2026" for a month, "?.?.2026" for a year. With no date at
 * all the season being looked at supplies the year, so every card says at least that much.
 */
export function dateBubbleText(date: string | null | undefined, fallbackYear: number | null | undefined): string {
  const text = (date ?? "").trim();
  const whole = plainDate(text);
  if (whole !== null) return formatDate(whole);
  const part = /^(\d{4})(?:-(\d{2}))?/.exec(text);
  const year = part ? part[1] : fallbackYear ? String(fallbackYear) : "?";
  const month = part?.[2] ?? "?";
  const style = whenFormats().date;
  if (style === "mdy") return `${month}/?/${year}`;
  if (style === "ymd") return `${year}-${month}-?`;
  return `?.${month}.${year}`;
}

/**
 * Whether a date is today or past (a whole date only: a month or a year alone is
 * not a day that can be said to have been reached). Colours a calendar card's
 * date bubble green once the show is under way.
 */
export function hasStarted(date: string | null | undefined, now: Date = new Date()): boolean {
  const starts = plainDate((date ?? "").trim());
  if (starts === null) return false;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return starts.getTime() <= today.getTime();
}

/**
 * Whether a card should say "this has started and you have not set it up" (build 222).
 *
 * The date pill goes away the moment the date is reached, which is right - it
 * was a countdown - but it left an anime added as a future entry and then
 * forgotten with nothing on its card at all. This takes the pill's place for
 * exactly that case: added before it started (no release chosen, so no group,
 * quality or name to look for), its date is today or past, and no file is in
 * the library. Anything with a file is somebody's anime already.
 */
export function setupDue(
  anime: {
    entry_mode?: string;
    air_start_date?: string | null;
    owned_episodes?: number;
    lifecycle?: string;
  },
  now: Date = new Date(),
): boolean {
  if (anime.entry_mode !== "future") return false;
  if (anime.lifecycle && anime.lifecycle !== "ACTIVE" && anime.lifecycle !== "UPCOMING") return false;
  if ((anime.owned_episodes ?? 0) > 0) return false;
  const starts = plainDate(anime.air_start_date ?? "");
  if (starts === null) return false;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return starts.getTime() <= today.getTime();
}

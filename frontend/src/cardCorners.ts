/**
 * What a watchlist card says in its corners and under its picture (build 151).
 *
 * The four health icons under the progress line are gone. In their place two
 * corners on the poster, each with its own territory:
 *
 * * top left, `!` - something needs attention. Red when a group is red,
 *   amber when the worst is yellow; absent when everything is green or does
 *   not apply.
 * * top right, a number - how many episodes AniFlow brought into the
 *   library since the watchlist was last left. First built as the newest
 *   episode's number, changed to a count the same day: *"if the user had 10
 *   animes, and he did not open the aniflow for 2 weeks and 2 episodes where
 *   downloaded, it should show (2)."*
 *
 * Kept free of React so the rules can be tested without rendering a card.
 */
import { t } from "./i18n";

export interface CornerGroup {
  colour: string;
  missing?: string[];
  not_applicable?: string | null;
}

export type CornerHealth = Record<string, CornerGroup | undefined> | undefined;

/** The four groups, in the order the attention list shows them. */
export const GROUPS = ["metadata", "download", "storage", "shoko"] as const;

/** `21 / 24`, `3 / ?` or `? / ?`.
 *
 * Unknown used to read "Episode count unknown", which is five words for one
 * fact and hid the one that *is* known: how many files are on disk.
 */
export function progressText(owned: number, count: number | null | undefined): string {
  if (count) return `${owned} / ${count}`;
  return `${owned} / ?`;
}

/** The counter's text, or null when there is nothing new. */
export function newEpisodeLabel(count: number | undefined): string | null {
  return count && count > 0 ? String(count) : null;
}

/** "today", "yesterday", "3 days ago" - days counted on the reader's clock. */
export function daysAgoText(when: string | null | undefined, now: Date = new Date()): string {
  if (!when) return t("card.when.recently");
  const then = new Date(when);
  if (Number.isNaN(then.getTime())) return t("card.when.recently");
  const midnight = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(then)) / 86_400_000);
  if (days <= 0) return t("card.when.today");
  if (days === 1) return t("card.when.yesterday");
  return t("card.when.days").replace("{n}", String(days));
}

/** The blue corner's tooltip: how many arrived, and when the latest did. */
export function newEpisodeTooltip(count: number, newestAt: string | null | undefined, now?: Date): string {
  const when = daysAgoText(newestAt, now);
  return count > 1
    ? `${t("card.new.titleMany")}\n${t("card.new.many").replace("{n}", String(count)).replace("{when}", when)}`
    : `${t("card.new.title")}\n${t("card.new.one").replace("{when}", when)}`;
}

/** The groups that need attention, worst first, then in the usual order. */
export function problemGroups(health: CornerHealth): string[] {
  const rank = (group: string) => (health?.[group]?.colour === "red" ? 0 : 1);
  return GROUPS
    .filter((group) => {
      const colour = health?.[group]?.colour;
      return colour === "red" || colour === "yellow";
    })
    .sort((a, b) => rank(a) - rank(b));
}

/** Red, amber, or nothing at all. */
export function attentionLevel(health: CornerHealth): "red" | "yellow" | null {
  const problems = problemGroups(health);
  if (problems.length === 0) return null;
  return health?.[problems[0]]?.colour === "red" ? "red" : "yellow";
}

/** One line under a group's name: what is missing, or why it does not apply. */
export function groupDetail(state: CornerGroup | undefined): string {
  if (!state) return t("health.colour.grey");
  if (state.not_applicable) return t(`health.na.${state.not_applicable}`);
  const missing = (state.missing ?? []).map((code) => t(`health.missing.${code}`));
  if (state.colour === "green" || missing.length === 0) return t("card.attention.fine");
  return `${t("card.attention.missing")} ${missing.join(", ")}`;
}

/** The red corner's tooltip: which groups, then how to see more. */
export function attentionTooltip(health: CornerHealth): string {
  const lines = problemGroups(health).map((group) =>
    t("card.attention.groupLine").replace("{group}", t(`health.group.${group}`)),
  );
  return [t("card.attention.title"), ...lines, t("card.attention.hint")].join("\n");
}

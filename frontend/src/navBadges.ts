/**
 * The small numbers on the sidebar (build 210).
 *
 * Watchlist: how many episodes arrived in the last day and have not been opened yet - the same
 * numbers as the blue corners on the cards, added up. In Progress: how many episodes are stuck,
 * red for a failure, amber for something waiting on you. Pure, so the rules can be tested without
 * a sidebar.
 */
import { stateClass } from "./progressState";

export interface NavBadge {
  /** What is drawn: "7", or "99+" for a very large number. */
  text: string;
  /** new = blue, error = red, warning = amber. */
  tone: "new" | "error" | "warning";
  count: number;
}

const CAP = 99;

function badge(count: number, tone: NavBadge["tone"]): NavBadge | null {
  if (!count || count < 1) return null;
  return { text: count > CAP ? `${CAP}+` : String(count), tone, count };
}

/** The blue number beside Watchlist, or null when nothing is new. */
export function watchlistBadge(items: readonly { new_episode_count?: number }[]): NavBadge | null {
  return badge(items.reduce((sum, item) => sum + (item.new_episode_count ?? 0), 0), "new");
}

/**
 * The number beside Checks (build 232): red with the count of things that must be fixed, else amber with the
 * count of things worth a look, and nothing at all when every check passes.
 */
export function checksBadge(summary: { errors: number; warnings: number } | null): NavBadge | null {
  if (!summary) return null;
  return summary.errors > 0 ? badge(summary.errors, "error") : badge(summary.warnings, "warning");
}

/**
 * The number beside In Progress. Red when anything failed, counting what failed; otherwise amber,
 * counting what is waiting on you (a stalled or missing download, a duplicate).
 */
export function progressBadge(items: readonly { state: string }[]): NavBadge | null {
  const errors = items.filter((item) => stateClass(item.state) === "sev-error").length;
  if (errors > 0) return badge(errors, "error");
  const warnings = items.filter((item) => stateClass(item.state) === "sev-warning").length;
  return badge(warnings, "warning");
}

import type { Rollover } from "./api/client";

/** How many anime the season check has something to say about. */
export function rolloverCount(rollover: Pick<Rollover, "earlier" | "airing">): number {
  return rollover.earlier.items.length + rollover.airing.items.length;
}

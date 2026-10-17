/**
 * What to do about one "needs your attention" record (build 100).
 *
 * Asked for: *"it shows the attention, but currently that's it - I would like
 * it to suggest how to fix it. If it's part of the timely fix it could say
 * 'this will probably be solved in the next scan'. Or if there is a way for
 * the user to fix it quicker, we could add a function."*
 *
 * The split between those two is the useful part, and it decides what a row
 * shows:
 *
 * * **waits** - AniFlow fixes it on its own. Saying so *is* the remedy, and
 *   asking somebody to act would waste their time.
 * * **acts** - one button resolves it, and it belongs on the row rather than
 *   three screens away.
 * * **decides** - a person has to choose, because either answer is
 *   defensible and AniFlow guessing is how a library goes quietly wrong.
 *
 * Kept apart from the page for the usual reason: which of the three a record
 * is, and which button it gets, is a fact about the record - answerable
 * without rendering anything. The full catalogue, including the sub-cases of
 * IMPORT_BLOCKED that still need splitting, is in
 * `docs/attention-catalogue.md`.
 */

/** What kind of answer a record has. */
export type RemedyKind = "waits" | "acts" | "decides";

/** One thing the row can offer to do. */
export interface RemedyAction {
  /** Which button: the page knows how to run these. */
  id:
    | "retry-download"
    | "change-release"
    | "test-feeds"
    | "setup-release"
    | "accept-metadata"
    | "dismiss";
  /** Whether it is the obvious one. Exactly one per record at most. */
  primary?: boolean;
}

export interface Remedy {
  kind: RemedyKind;
  /** Translation key for the sentence that says what to expect or do. */
  message: string;
  actions: RemedyAction[];
}

/**
 * The waiting cases, and what they are waiting for.
 *
 * Each names the pass that will fix it, because "soon" is not information and
 * "the next scan" is - the user knows what a scan is and roughly when one
 * happens.
 */
const WAITS: Record<string, Remedy> = {
  DOWNLOAD_MISSING: {
    kind: "waits",
    message: "attention.fix.DOWNLOAD_MISSING",
    // Impatience is legitimate: the queue pass is half-hourly.
    actions: [{ id: "retry-download", primary: true }],
  },
  FEED_FAILED: {
    kind: "waits",
    message: "attention.fix.FEED_FAILED",
    actions: [{ id: "test-feeds" }],
  },
  NO_RELEASES: {
    kind: "waits",
    message: "attention.fix.NO_RELEASES",
    // The real fix when the name is wrong rather than the season early.
    actions: [{ id: "change-release", primary: true }],
  },
};

const ACTS: Record<string, Remedy> = {
  DOWNLOAD_STALLED: {
    kind: "acts",
    message: "attention.fix.DOWNLOAD_STALLED",
    actions: [{ id: "change-release", primary: true }, { id: "dismiss" }],
  },
  NEEDS_CONVERSION: {
    kind: "acts",
    message: "attention.fix.NEEDS_CONVERSION",
    actions: [{ id: "setup-release", primary: true }],
  },
  IMPORT_BLOCKED: {
    kind: "acts",
    message: "attention.fix.IMPORT_BLOCKED",
    // One kind with twelve origins today; the sub-cases and their own
    // buttons are written up in the catalogue and not built yet.
    actions: [],
  },
  METADATA_CHANGED: {
    kind: "acts",
    message: "attention.fix.METADATA_CHANGED",
    // Both halves of the answer the request asked for: *"With a Fix button
    // to allow the change or Skip the change and keep the user
    // modification."*  Skip is Dismiss - every record has it, and here it
    // means something specific, so it is listed rather than assumed.
    actions: [{ id: "accept-metadata", primary: true }, { id: "dismiss" }],
  },
};

const DECIDES: Record<string, Remedy> = {
  NEW_VERSION: {
    kind: "decides",
    message: "attention.fix.NEW_VERSION",
    // The page already draws this one's two buttons itself.
    actions: [],
  },
  AMBIGUOUS_RELEASE: {
    kind: "decides",
    message: "attention.fix.AMBIGUOUS_RELEASE",
    actions: [],
  },
  // Not really a fork in the road - there is no second option to weigh - but
  // it is not "acts" either, since no button here does anything AniFlow can
  // finish on its own. Dismiss is the only real move, the same shape as
  // AMBIGUOUS_RELEASE's own "decides" (build 168).
  EPISODE_NOT_IDENTIFIABLE: {
    kind: "decides",
    message: "attention.fix.EPISODE_NOT_IDENTIFIABLE",
    actions: [],
  },
  // Which file to keep is the user's: both are real, and AniFlow deleting
  // either would be a guess about somebody's media (build 189). The two
  // delete buttons are drawn by the page, like NEW_VERSION's.
  DUPLICATE_FILES: {
    kind: "decides",
    message: "attention.fix.DUPLICATE_FILES",
    actions: [],
  },
};

/** What to do about this record, or null for a kind nothing is known about. */
export function remedyFor(kind: string): Remedy | null {
  return WAITS[kind] ?? ACTS[kind] ?? DECIDES[kind] ?? null;
}

/** Whether this record will fix itself, which is worth saying plainly. */
export function resolvesItself(kind: string): boolean {
  return remedyFor(kind)?.kind === "waits";
}

/** Every kind that has a remedy, for the guard that keeps this complete. */
export function kindsWithRemedies(): string[] {
  return [...Object.keys(WAITS), ...Object.keys(ACTS), ...Object.keys(DECIDES)].sort();
}

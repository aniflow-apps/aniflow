/**
 * How serious one record is, and therefore what colour its tag is (127).
 *
 * Asked for after the sample records made every kind visible at once:
 *
 * > Some tags have not colors like Feed failed, nobody is seeding this is not
 * > even a tag. ... Then please go ahead and color code the tags yourself.
 * > I would do something like Error = red, Warning = yellow, Information =
 * > blue, notification = white.
 *
 * The colours were per-kind CSS classes (`chip kind-feed_failed`), which meant
 * a kind with no class of its own had no colour - and nine kinds shared four
 * classes by accident rather than by rule. This is the rule instead: one
 * severity per kind, in one table, with a test that every kind has one.
 *
 * Five rather than the four suggested, and the extra one is **success**. It is
 * green because that was already asked for once, about the same idea: *"lets
 * make the 'In library' green, because its something good."*  An episode
 * arriving is the most common entry in the log and the only one that is good
 * news; colouring it the same as "a file was renamed" would waste the one
 * distinction people actually scan for.
 *
 * What each one means
 * -------------------
 *
 * * **error** - something failed and will not fix itself. Somebody has to act.
 * * **warning** - something is wrong but AniFlow is handling it, or it may
 *   clear on its own. Worth knowing, not worth interrupting for.
 * * **info** - a decision is waiting, or a normal thing happened that is worth
 *   a line. Nothing is broken.
 * * **success** - it worked.
 * * **notice** - bookkeeping. True, uninteresting, and deliberately quiet.
 *
 * The distinction that matters most is error against warning, because it is
 * the one that decides whether somebody should stop what they are doing. A
 * download nobody is seeding is *not* an error: AniFlow retries, and calling
 * it one is how a list of red rows stops meaning anything - the mistake build
 * 118 had to undo when every Shoko half of every card went red.
 */

export type Severity = "error" | "warning" | "info" | "success" | "notice";

/**
 * Attention kinds. models/activity.py:AttentionKind.
 *
 * Nothing here is `success` or `notice`: a record on this page exists because
 * something is unresolved, so the quiet severities would be a contradiction.
 */
const ATTENTION: Record<string, Severity> = {
  // A finished file could not be placed. It stays where it is until somebody
  // deals with it, and the library is missing an episode it downloaded.
  IMPORT_BLOCKED: "error",

  // All four of these are real problems that AniFlow keeps working on: the
  // queue retries a vanished torrent, the next scan retries a feed, and a
  // release that nothing matched may simply not be published yet.
  DOWNLOAD_MISSING: "warning",
  FEED_FAILED: "warning",
  DOWNLOAD_STALLED: "warning",
  NO_RELEASES: "warning",

  // A person has to choose. Either answer is defensible, which is exactly why
  // these are not warnings - nothing is wrong with any of them.
  NEW_VERSION: "info",
  AMBIGUOUS_RELEASE: "info",
  NEEDS_CONVERSION: "info",
  METADATA_CHANGED: "info",
  // AniFlow already knows the anime; it just could not read one episode
  // number out of the name, most often because the release is a batch.
  // Nothing failed - there is simply nothing to queue automatically.
  EPISODE_NOT_IDENTIFIABLE: "info",
  // Two files for one episode (build 189). Nothing is lost, but the library
  // has the same episode twice until somebody chooses - yellow, not blue.
  DUPLICATE_FILES: "warning",
};

/** Activity kinds. services/activity_kinds.py. */
const ACTIVITY: Record<string, Severity> = {
  // It worked. The one line in the log that is good news.
  EPISODE_ACQUIRED: "success",

  // It did not, and nothing else is going to fix it.
  EPISODE_BLOCKED: "error",
  // An add that failed. Red because the show is not on the watchlist and
  // nothing will retry by itself.
  ADD_FAILED: "error",
  // You told AniFlow which anime a release belonged to and the release did
  // not match that anime's profile, so nothing was queued.
  ASSIGN_REJECTED: "error",

  // Worth knowing. None of them stops anything working.
  INDEXER_UNREACHABLE: "warning",
  INDEXER_MISSING: "warning",
  TORRENT_UNCLAIMED: "warning",
  LIBRARY_UNPLACED: "warning",
  LIBRARY_SUPERSEDED_LEFT: "warning",
  LIBRARY_DUPLICATE_DELETED: "info",

  // Normal things, worth a line each.
  RSS_EPISODE_FOUND: "info",
  LIBRARY_ADOPTED: "info",
  LIBRARY_SUPERSEDED: "info",
  FILES_RENAMED: "info",
  RELEASE_LOOKUP_FAILED: "error",
  LIBRARY_MOVED: "info",
  // Worth a look: it usually means Move on Import is still on in Shoko.
  LIBRARY_MOVED_BY_SHOKO: "warning",
  TORRENT_ADOPTED: "info",
  DOWNLOAD_APPROVED: "info",
  DUPLICATE_RESOLVED: "info",
  ASSIGN_MATCHED: "info",

  // Bookkeeping: the half-hourly pass reporting what it put right.
  RECONCILED: "notice",
};

/**
 * The severity of one record.
 *
 * Falls back to `info` rather than to nothing, so a kind added on the server
 * before this table is updated still gets a readable tag instead of an
 * uncoloured one. The test in severity.test.ts is what stops that fallback
 * becoming the normal case.
 */
export function severityOf(kind: string): Severity {
  return ATTENTION[kind] ?? ACTIVITY[kind] ?? "info";
}

/** The class the tag carries. */
export function severityClass(kind: string): string {
  return `sev-${severityOf(kind)}`;
}

/** Every kind this table knows, for the completeness test. */
export function ratedKinds(): string[] {
  return [...Object.keys(ATTENTION), ...Object.keys(ACTIVITY)].sort();
}

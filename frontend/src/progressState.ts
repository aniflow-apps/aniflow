/**
 * A colour per episode_view.py state, for In Progress and its Archive (166).
 *
 * Not the guarded severity table Attention and Activity share
 * (severity.ts) - most rows here are simply "in progress", not "a problem",
 * so that table's own distinction does not apply the same way. Shared
 * between the two pages so a colour cannot drift between them.
 *
 * Reported live: *"I noticed the downloading tag is now grey and it was
 * before blue, I liked the blue ... Maybe we can have all steps in a certain
 * color ... something like working yellow, error red, done green."* Settled
 * on four of the five severities already in the palette rather than a fifth:
 * blue for anything actively moving or awaiting a decision, yellow for stuck
 * but still self-healing, red for a real failure, green for done.
 */
export function stateClass(state: string): string {
  if (state === "complete") return "sev-success";
  if (state === "failed" || state === "file_gone") return "sev-error";
  if (state === "download_missing" || state === "download_stalled" || state === "duplicate_files") return "sev-warning";
  // downloading, ready, moving_to_library, moving_to_shoko, with_shoko,
  // new_version - all "something is happening, or waiting on you".
  return "sev-info";
}

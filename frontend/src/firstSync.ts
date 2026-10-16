/**
 * The install wizard's last step, run for real (build 226).
 *
 * The step used to *describe* three jobs - the titles database, the airing
 * calendar, the library scan - and mark each "in the background". Nothing
 * started any of them: the worker fetches the titles on its own about half a
 * minute after boot, the calendar's automatic update is off by default so it
 * stayed empty with an "Update calendar" button, and no scan ran at all. The
 * user finished the wizard into an empty AniFlow.
 *
 * So the wizard runs them, one after another, and reports what is actually
 * happening. The order is not decoration: the titles database and the id map
 * that arrives with it are what let the calendar link its entries to AniDB
 * ids, so a calendar fetched first has to be fetched again to be linked.
 *
 * Pure on purpose - every call to the server comes in through `deps`, so the
 * ordering and the failure handling are tested without a network.
 */
export type FirstSyncStepId = "titles" | "calendar" | "library";
export type FirstSyncState = "waiting" | "running" | "done" | "failed";

export interface FirstSyncStep {
  id: FirstSyncStepId;
  state: FirstSyncState;
  /** Why it failed. Empty otherwise: while it runs the row shows a bar, not words. */
  detail: string;
  /** How far along, 0-1, while the step can say; null or missing when it cannot (the bar then just moves). */
  progress?: number | null;
  /** How far the piece being worked on reaches, 0-1: the yellow part of the bar, ahead of the green. */
  reach?: number | null;
}

/** What the calendar reports about its own update. */
export interface CalendarProgressInput {
  done: number;
  total: number;
  sources_done: number;
  sources_total: number;
  pictures: number;
  pictures_total: number;
}

/**
 * How far the calendar's update has got, from what it says about itself.
 *
 * The update walks several seasons; each asks its sources, then fetches the
 * posters. A season is counted as half sources and half posters, and the
 * poster half only starts once the number of posters is known - a bar that
 * guesses its own length is worse than none. ``reach`` is the end of the
 * season being worked on, so the bar can show the chunk in progress.
 *
 * Null when the update has not said how many seasons it has (yet).
 */
export function calendarProgress(state: Partial<CalendarProgressInput>): { progress: number; reach: number } | null {
  const total = state.total ?? 0;
  if (!total || total < 0) return null;
  const done = Math.max(0, state.done ?? 0);
  const sources = state.sources_total ? Math.min(1, (state.sources_done ?? 0) / state.sources_total) : 0;
  const within = state.pictures_total
    ? 0.5 + 0.5 * Math.min(1, (state.pictures ?? 0) / state.pictures_total)
    : 0.5 * sources;
  return {
    progress: Math.min(1, (done + within) / total),
    reach: Math.min(1, (done + 1) / total),
  };
}

export interface FirstSyncDeps {
  /** Ask the worker to run a scheduled task now. */
  runTask: (id: string) => Promise<{ started: boolean }>;
  /** Where that task is: running / asked for / its last error. */
  taskState: (id: string) => Promise<{ running: boolean; requested: boolean; last_error: string | null } | null>;
  /** Start the calendar's update of the current and coming seasons. */
  startCalendar: () => Promise<void>;
  calendarState: () => Promise<
    { running: boolean; error: string | null; blocked_reason: string | null; summary?: string } & Partial<CalendarProgressInput>
  >;
  /** Re-read the library folders against the disk. Reads and links; moves nothing. */
  scanLibraries: () => Promise<void>;
  wait: (ms: number) => Promise<void>;
  /** True once the page has gone, so a poll stops rather than running for ever. */
  cancelled: () => boolean;
}

/** The steps for this install, in the order they run. */
export function firstSyncSteps(): FirstSyncStepId[] {
  return ["titles", "calendar", "library"];
}

export function initialSteps(): FirstSyncStep[] {
  return firstSyncSteps().map((id) => ({ id, state: "waiting", detail: "", progress: null, reach: null }));
}

/** Whether Open AniFlow may be pressed: every step finished. A failed one blocks until retried or skipped. */
export function allDone(steps: FirstSyncStep[]): boolean {
  return steps.every((step) => step.state === "done");
}

const POLL_MS = 1500;

async function untilTaskIdle(id: string, deps: FirstSyncDeps): Promise<string | null> {
  // A request sits as "requested" until the worker's next tick takes it, so
  // "not running" alone would read as finished before it began.
  let sawIt = false;
  for (let waited = 0; !deps.cancelled(); waited += POLL_MS) {
    const state = await deps.taskState(id);
    if (state === null) return null;
    if (state.running || state.requested) sawIt = true;
    else if (sawIt || waited >= 15_000) return state.last_error;
    await deps.wait(POLL_MS);
  }
  return null;
}

async function runStep(
  id: FirstSyncStepId,
  deps: FirstSyncDeps,
  say: (how: { progress: number; reach: number } | null) => void,
): Promise<string | null> {
  if (id === "titles") {
    const asked = await deps.runTask("anidb_identity_refresh");
    // Not startable (the task is not manual, or is gone): nothing to wait for.
    if (!asked.started) return null;
    return untilTaskIdle("anidb_identity_refresh", deps);
  }
  if (id === "calendar") {
    await deps.startCalendar();
    // Never backwards: a new season restarting its own counters must not pull the bar back.
    let furthest = 0;
    for (;;) {
      if (deps.cancelled()) return null;
      const state = await deps.calendarState();
      if (state.blocked_reason) return state.blocked_reason;
      if (!state.running) return state.error;
      const how = calendarProgress(state);
      if (how) furthest = Math.max(furthest, how.progress);
      say(how ? { progress: furthest, reach: Math.max(how.reach, furthest) } : null);
      await deps.wait(POLL_MS);
    }
  }
  await deps.scanLibraries();
  return null;
}

/**
 * Run the steps in order from `from`, calling `onChange` with the whole list
 * each time something moves. Stops at the first failure and leaves it marked,
 * so the page can offer Retry and the steps after it stay waiting.
 */
export async function runFirstSync(
  deps: FirstSyncDeps,
  onChange: (steps: FirstSyncStep[]) => void,
  steps: FirstSyncStep[] = initialSteps(),
): Promise<FirstSyncStep[]> {
  const current = steps.map((step) => ({ ...step }));
  const publish = () => onChange(current.map((step) => ({ ...step })));
  for (const step of current) {
    if (step.state === "done") continue;
    step.state = "running";
    step.detail = "";
    step.progress = null;
    step.reach = null;
    publish();
    let problem: string | null;
    try {
      problem = await runStep(step.id, deps, (how) => {
        step.progress = how ? how.progress : null;
        step.reach = how ? how.reach : null;
        publish();
      });
    } catch (reason) {
      problem = reason instanceof Error ? reason.message : String(reason);
    }
    if (deps.cancelled()) return current;
    if (problem) {
      step.state = "failed";
      step.detail = problem;
      publish();
      return current;
    }
    step.state = "done";
    step.detail = "";
    step.progress = 1;
    step.reach = 1;
    publish();
  }
  return current;
}

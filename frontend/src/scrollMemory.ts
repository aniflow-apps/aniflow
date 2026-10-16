/**
 * Where the watchlist was scrolled to, so coming back to it is where you left it
 * (build 198).
 *
 * Reported: open an anime from far down the watchlist, press back, and you are
 * somewhere else - the list unmounts when the detail page opens, and the page
 * had nothing to remember it with.
 *
 * The position is taken at the moment of navigating away, while the old page is
 * still on screen. Listening to scroll events instead loses to the browser: the
 * moment the shorter detail page replaces the list, the browser clamps the
 * scroll to 0 and a listener that is still attached records it.
 */
const positions = new Map<string, number>();

export function rememberScroll(key: string, y: number): void {
  positions.set(key, Math.max(0, Math.round(y)));
}

export function recallScroll(key: string): number {
  return positions.get(key) ?? 0;
}

/** What `restoreScroll` needs from the browser - injectable so it can be tested. */
export interface ScrollEnvironment {
  y: () => number;
  scrollTo: (y: number) => void;
  /** Calls back on the next frame; returns a way to cancel. */
  frame: (callback: () => void) => () => void;
}

/**
 * Put the page back at ``target``, and keep it there while the page settles.
 *
 * The list is in memory, but parts of it - the library groups, the covers -
 * arrive a moment after it is drawn and move things, so the position is set
 * again on each frame for a short while. It stops the moment the *user*
 * scrolls (the page is somewhere other than where it was just put), so it never
 * fights anybody.
 */
export function restoreScroll(target: number, env: ScrollEnvironment, frames = 30): () => void {
  if (target <= 0) return () => undefined;
  let left = frames;
  let lastSet: number | null = null;
  let cancelFrame: () => void = () => undefined;
  let stopped = false;

  const step = () => {
    if (stopped) return;
    if (lastSet !== null && Math.abs(env.y() - lastSet) > 40) return; // the user took over
    env.scrollTo(target);
    lastSet = env.y();
    left -= 1;
    if (left > 0) cancelFrame = env.frame(step);
  };
  cancelFrame = env.frame(step);
  return () => {
    stopped = true;
    cancelFrame();
  };
}

/** The real browser, for the app. */
export const browserScroll: ScrollEnvironment = {
  y: () => window.scrollY,
  scrollTo: (y) => window.scrollTo(0, y),
  frame: (callback) => {
    const id = window.requestAnimationFrame(callback);
    return () => window.cancelAnimationFrame(id);
  },
};

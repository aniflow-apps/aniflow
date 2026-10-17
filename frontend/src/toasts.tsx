import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

/**
 * Short-lived messages that do not interrupt anything (build 77).
 *
 * The problem this solves: adding an anime worked and said nothing, so the
 * only way to know was to look for it in the list. The problem it must not
 * create: a dialog with an OK button, which turns "that worked" into a thing
 * you have to acknowledge.
 *
 * So: a small note in a corner, gone on its own in a few seconds, with no
 * button to press. Four seconds for good news - long enough to read a
 * sentence, short enough not to sit in the way - and eight for a failure,
 * because a failure is usually a sentence you want to finish reading and may
 * want to copy. Both are dismissable by clicking, for anyone who would rather
 * not wait.
 *
 * Which corner is a setting, because the answer depends on the screen: on a
 * wide monitor the top right is out of the way, on a laptop the bottom right
 * is nearer the thing you just clicked.
 */

export type ToastKind = "success" | "error" | "info";
export type ToastCorner = "top-right" | "top-left" | "bottom-right" | "bottom-left";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  /**
   * A caller's own name for this notification, so it can be written to again.
   *
   * Without it there is no way to say "the thing I showed you a moment ago is
   * now at 40 of 200": the caller polling a job has a key, not the id the
   * provider handed out.
   */
  key?: string;
  /** Present when this notification carries a bar. */
  progress?: { done: number; total: number };
  /**
   * True while the work is still running: no timer at all.
   *
   * A notification that describes something in flight has to outlast four
   * seconds, or it disappears while the thing it is about is still happening.
   */
  pinned?: boolean;
}

/** How long each kind stays, in milliseconds - or null to stay put. */
const LIFETIME: Record<ToastKind, number | null> = {
  success: 4000,
  info: 4000,
  /* Longer than the others, and it still fades: a notification is a
     notification. What makes an error readable afterwards is the Activity log,
     which now records the ones AniFlow knows about - see
     api/routes/anime.py:_add_failed (build 140). */
  error: 12000,
};

/** More than this on screen at once is a log, not a notification. */
const MAX_VISIBLE = 4;

interface ToastApi {
  show: (message: string, kind?: ToastKind) => void;
  /**
   * Show or update a notification with a bar, and keep it on screen.
   *
   * Called repeatedly with the same key - once per poll - and never expires
   * on its own. `finish` is what lets it go.
   */
  progress: (key: string, update: { message: string; done: number; total: number }) => void;
  /**
   * The work is over: fill the bar, say so, and let the notification fade on
   * the ordinary timer. Unknown keys are ignored, so a caller may call this
   * without having to remember whether it ever started one.
   */
  finish: (key: string, message?: string) => void;
}

const ToastContext = createContext<ToastApi>({
  show: () => undefined,
  progress: () => undefined,
  finish: () => undefined,
});

/** Raise a notification from anywhere below the provider. */
export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({
  corner = "top-right",
  children,
}: {
  corner?: ToastCorner;
  children: React.ReactNode;
}) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  /**
   * Which notification a caller's key is showing in (build 149).
   *
   * The bug this fixes: `finish` read the id out of a `setToasts` updater and
   * then used it on the next line to start the fade timer. A state updater
   * does not run when it is called - React runs it while rendering - so the
   * variable was still null, the timer was never set, and *"Finished. 8
   * snapshots applied, 7 refused"* sat on screen until the page was
   * reloaded. Reported against 148 and wrong since 145; the window being open
   * is what hid it for three builds.
   *
   * A ref, because `finish` needs the answer synchronously and state cannot
   * give it one.
   */
  const idsByKey = useRef(new Map<string, number>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    for (const [key, held] of idsByKey.current) {
      if (held === id) idsByKey.current.delete(key);
    }
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (message: string, kind: ToastKind = "success") => {
      const id = nextId.current++;
      // The oldest goes rather than the newest being dropped: the newest is
      // the one describing what just happened.
      setToasts((current) => [...current, { id, kind, message }].slice(-MAX_VISIBLE));
      const lifetime = LIFETIME[kind];
      if (lifetime !== null) {
        timers.current.set(id, setTimeout(() => dismiss(id), lifetime));
      }
    },
    [dismiss],
  );

  const progress = useCallback(
    (key: string, update: { message: string; done: number; total: number }) => {
      setToasts((current) => {
        const existing = current.find((toast) => toast.key === key);
        if (existing) {
          return current.map((toast) =>
            toast.key === key
              ? { ...toast, message: update.message, progress: { done: update.done, total: update.total } }
              : toast,
          );
        }
        const id = nextId.current++;
        idsByKey.current.set(key, id);
        return [
          ...current,
          {
            id,
            kind: "info" as ToastKind,
            message: update.message,
            key,
            progress: { done: update.done, total: update.total },
            pinned: true,
          },
        ].slice(-MAX_VISIBLE);
      });
    },
    [],
  );

  const finish = useCallback(
    (key: string, message?: string) => {
      // Read before the update rather than out of it - see `idsByKey`. An
      // unknown key is not an error: a caller may finish something it never
      // started rather than having to remember whether it did.
      const target = idsByKey.current.get(key);
      if (target === undefined) return;
      setToasts((current) =>
        current.map((toast) =>
          toast.key === key
            ? {
                ...toast,
                pinned: false,
                message: message ?? toast.message,
                // Filled, whatever the last poll said: the work is done, and
                // a bar left at 97% reads as something having gone wrong.
                progress: toast.progress
                  ? { done: toast.progress.total, total: toast.progress.total }
                  : undefined,
              }
            : toast,
        ),
      );
      if (!timers.current.has(target)) {
        timers.current.set(target, setTimeout(() => dismiss(target), LIFETIME.info ?? 4000));
      }
    },
    [dismiss],
  );

  // Timers outlive a re-render but must not outlive the provider.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => clearTimeout(timer));
      pending.clear();
    };
  }, []);

  const api = useMemo(() => ({ show, progress, finish }), [show, progress, finish]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* aria-live so a screen reader hears what a sighted user sees; polite
          because none of this is urgent enough to interrupt. */}
      <div className={`toast-stack ${corner}`} role="status" aria-live="polite">
        {toasts.map((toast) => (
          <button
            type="button"
            key={toast.id}
            className={`toast toast-${toast.kind}${toast.progress ? " toast-progress" : ""}`}
            onClick={() => dismiss(toast.id)}
          >
            <span>{toast.message}</span>
            {/* The same bar the update window uses, for the same reason: it
                counts work items, so it cannot run backwards. */}
            {toast.progress && (
              <span
                className="update-bar-track"
                role="progressbar"
                aria-valuenow={toast.progress.done}
                aria-valuemin={0}
                aria-valuemax={toast.progress.total || 1}
              >
                <span
                  className="update-bar-fill"
                  style={{
                    width: `${toast.progress.total > 0 ? Math.min(100, Math.round((toast.progress.done / toast.progress.total) * 100)) : 0}%`,
                  }}
                />
              </span>
            )}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

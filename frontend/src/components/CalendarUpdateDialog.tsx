/**
 * Watching an update happen (build 145).
 *
 * Pressing Update used to put a sentence on the page and leave you there:
 *
 *     *"instead of just having a text saying it updates please wait, and you
 *     do not even have a loading bar. The debug menu actually shows some
 *     stuff."*
 *
 * Right - the debug page was the only place the work was visible, and that is
 * the wrong way round. So the button opens this, and it shows the same things
 * the debug page does, for the one season being fetched.
 *
 * **What the bar is attached to.** An update is two phases with very
 * different shapes, so it is two bars rather than one guess:
 *
 *  - *Sources*: how many have been asked, out of how many there are. Known
 *    before anything starts, so this bar is honest from the first moment.
 *  - *Artwork*: posters fetched, out of the number chosen. Not known until
 *    the snapshots are in - one at a time over a network, and on a first run
 *    the part that actually takes the time - so the bar appears when the
 *    number does rather than pretending to know it earlier.
 *
 * Neither bar is a clock. They count work items, which is the only thing here
 * that can be counted honestly.
 *
 * **The trace is behind `debug_mode` (build 148).** It was the right thing to
 * show while v2 was new and being proved, and the wrong thing to leave on a
 * finished feature: `conflicts=125 · anidb=96` answers a question nobody
 * watching an update is asking, and "mal not asked ... no credential for this
 * source" reads as a fault rather than as a setting. What is left is the two
 * bars, the step and the summary - which is the whole of what an update means
 * to somebody waiting for one.
 */
import { useCallback, useEffect, useState } from "react";

import { calendarStatus, type CalendarStatus, type CalendarTraceEvent, calendarDebug } from "../api/client";
import { stepText } from "../calendarView";
import { formatTime } from "../formatWhen";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

const TICK_MS = 1000;

/** How many trace lines to keep on screen. The dialog is not the log. */
const LINES = 12;

function Bar({ done, total, label }: { done: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className="update-bar">
      <div className="update-bar-head">
        <span>{label}</span>
        <small className="muted">{total > 0 ? `${done} / ${total}` : "…"}</small>
      </div>
      {/* role=progressbar so a screen reader gets the number, not the colour. */}
      <div
        className="update-bar-track"
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total || 1}
        aria-label={label}
      >
        <span className="update-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function CalendarUpdateDialog({
  season,
  year,
  debugMode = false,
  onClose,
  onFinished,
}: {
  season: string;
  year: number;
  /** Whether to show the trace. Off everywhere but a debug installation. */
  debugMode?: boolean;
  onClose: () => void;
  onFinished: () => void;
}) {
  const [job, setJob] = useState<CalendarStatus | null>(null);
  const [events, setEvents] = useState<CalendarTraceEvent[]>([]);
  const [done, setDone] = useState(false);

  // Build 124's rule: a dialog holds the page behind it still. The CSS that
  // looks like it covers this only stops scroll chaining once the backdrop
  // has scrolled to its own end, and this one usually fits.
  useLockedPageScroll();

  const read = useCallback(async () => {
    try {
      /* The trace is a second request, and it is only asked for when
         something is going to draw it. An ordinary installation polls once a
         second for the bars and nothing else. */
      const state = await calendarStatus();
      setJob(state);
      if (debugMode) {
        const debug = await calendarDebug();
        setEvents(debug.events.slice(0, LINES));
      }
      if (!state.running) {
        setDone(true);
        onFinished();
      }
    } catch {
      /* A poll that fails changes nothing on screen; the next one tries again. */
    }
  }, [debugMode, onFinished]);

  useEffect(() => { void read(); }, [read]);

  useEffect(() => {
    if (done) return;
    const timer = window.setInterval(() => { void read(); }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [done, read]);

  const artwork = job?.step === "artwork";

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <section className="dialog calendar-update">
        <div className="dialog-head">
          <h2>{t("calendarUpdate.title").replace("{season}", `${season} ${year}`)}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </div>

        {/* The two phases. Artwork only once there is a number behind it. */}
        <Bar
          done={job?.sources_done ?? 0}
          total={job?.sources_total ?? 0}
          label={t("calendarUpdate.sources")}
        />
        {(artwork || (job?.pictures_total ?? 0) > 0) && (
          <Bar
            done={job?.pictures ?? 0}
            total={job?.pictures_total ?? 0}
            label={t("calendarUpdate.artwork")}
          />
        )}

        <p className="muted">
          {done
            ? job?.error
              ? t("calendarUpdate.failed").replace("{error}", job.error)
              : t("calendarUpdate.finished").replace("{summary}", job?.summary ?? "")
            : job?.step === "artwork"
            ? t("calendar.stepArtwork").replace("{count}", String(job?.pictures ?? 0))
            : job?.step
            ? stepText(job.step, debugMode)
            : t("calendarDebug.starting")}
        </p>

        {/* The same trace the debug page shows, trimmed to what fits - and
            only on a debug installation since build 148. */}
        {debugMode && <ul className="trace-list">
          {events.map((event, index) => (
            <li key={`${event.at}-${index}`} className={`trace-${event.level}`}>
              <code>{formatTime(event.at, true)}</code>
              <strong>{event.stage}</strong>
              <span>{event.message}</span>
              {Object.keys(event.detail).length > 0 && (
                <small className="muted">
                  {Object.entries(event.detail).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}
                </small>
              )}
            </li>
          ))}
          {!events.length && <li className="muted">{t("calendarDebug.starting")}</li>}
        </ul>}

        {/* The note beside the button rather than under it, and the row
            given room above it (build 149): *"add a bit of breathing space
            between the text and the Hide button"*, and *"move 'Hiding this
            does not stop the update' next to the hide button"*. It is a
            caption for that button, so it belongs on its line. */}
        <div className="action-stack horizontal update-actions">
          <button className="button primary" onClick={onClose}>
            {done ? t("calendarUpdate.close") : t("calendarUpdate.hide")}
          </button>
          {!done && <small className="muted">{t("calendarUpdate.keepsGoing")}</small>}
        </div>
      </section>
    </div>
  );
}

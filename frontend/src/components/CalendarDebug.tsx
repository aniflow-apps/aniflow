/**
 * What the calendar is doing, while it does it (build 144).
 *
 * Asked for during the v2 rewrite: *"we need a way better debug method for the
 * calendar, this can be temp, but while working on it, I would like to see
 * what is happening"*. The container log was the only answer, and it needs
 * shell access, arrives interleaved with everything else AniFlow does, and is
 * gone when it scrolls.
 *
 * Four things, in the order somebody actually asks them:
 *
 *  1. **What is it doing right now** - the running job, the source being
 *     asked, the posters counted.
 *  2. **What has it got** - works, identities, and one row per season.
 *  3. **Is a source in trouble** - last success, consecutive failures, the
 *     last error in full.
 *  4. **What just happened** - the trace, newest first.
 *
 * Temporary by intent. If v2 settles and nobody misses this, it and
 * services/calendar_trace.py are deleted together and nothing else changes.
 */
import { useCallback, useEffect, useState } from "react";

import { calendarDebug, clearCalendarTrace, type CalendarDebug as DebugPayload } from "../api/client";
import { formatTime } from "../formatWhen";
import { t } from "../i18n";

/** How often to re-read while something is running. */
const TICK_MS = 2000;

function moment(value: string | null | undefined): string {
  if (!value) return "—";
  return formatTime(value, true);
}

function age(value: string | null | undefined): string {
  if (!value) return t("calendarDebug.never");
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 90) return t("calendarDebug.secondsAgo").replace("{n}", String(seconds));
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return t("calendarDebug.minutesAgo").replace("{n}", String(minutes));
  return t("calendarDebug.hoursAgo").replace("{n}", String(Math.round(minutes / 60)));
}

export function CalendarDebugPage({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<DebugPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = useCallback(async () => {
    try {
      setData(await calendarDebug());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void read(); }, [read]);

  /* Polled only while there is something to watch, and on a timer slow enough
     that the page is not itself the busiest thing in the log. */
  useEffect(() => {
    if (!data?.status.running) return;
    const timer = window.setInterval(() => { void read(); }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [data?.status.running, read]);

  const job = data?.status;

  return (
    <section className="panel">
      <div className="dialog-head">
        <h2>{t("calendarDebug.title")}</h2>
        <button className="close" onClick={onBack} aria-label={t("add.cancel")}>×</button>
      </div>
      <p className="muted">{t("calendarDebug.hint")}</p>

      {error && <p className="error-message">{error}</p>}

      <div className="action-stack horizontal">
        <button className="button secondary" onClick={() => void read()}>
          {t("calendarDebug.refresh")}
        </button>
        <button
          className="button secondary"
          onClick={async () => { await clearCalendarTrace().then(setData).catch(() => undefined); }}
        >
          {t("calendarDebug.clear")}
        </button>
      </div>

      {/* 1. Right now */}
      <h3>{t("calendarDebug.now")}</h3>
      {job?.running ? (
        <p className="notice-message">
          {t("calendarDebug.running")
            .replace("{kind}", job.kind || "update")
            .replace("{season}", job.season ? `${job.season} ${job.year}` : "—")}
          <small className="muted" style={{ display: "block", marginTop: 4 }}>
            {job.step === "artwork"
              ? t("calendar.stepArtwork").replace("{count}", String(job.pictures))
              : job.step
              ? t("calendar.stepSource").replace("{source}", job.step)
              : t("calendarDebug.starting")}
            {job.total > 0 ? ` · ${job.done}/${job.total}` : ""}
          </small>
        </p>
      ) : (
        <p className="muted">
          {job?.error
            ? t("calendarDebug.lastFailed").replace("{error}", job.error)
            : job?.summary
            ? t("calendarDebug.lastFinished").replace("{summary}", job.summary)
            : t("calendarDebug.idle")}
        </p>
      )}
      {job?.blocked_reason && <p className="error-message">{job.blocked_reason}</p>}
      {data && !data.auto_update && (
        <p className="muted">{t("calendarDebug.autoOff")}</p>
      )}

      {/* 2. What it holds */}
      <h3>{t("calendarDebug.stored")}</h3>
      <p className="muted">
        {t("calendarDebug.counts")
          .replace("{works}", String(data?.works ?? 0))
          .replace("{identities}", String(data?.identities ?? 0))}
      </p>
      <div className="table-scroll">
        <table className="sources-table">
          <thead>
            <tr>
              <th>{t("calendarDebug.colSeason")}</th>
              <th>{t("calendarDebug.colShows")}</th>
              <th>{t("calendarDebug.colTried")}</th>
              <th>{t("calendarDebug.colLoaded")}</th>
            </tr>
          </thead>
          <tbody>
            {(data?.seasons ?? []).map((row) => (
              <tr key={`${row.year}-${row.season}`}>
                <td>{row.season} {row.year}</td>
                <td>{row.works}</td>
                <td>{age(row.last_attempt_at)}</td>
                <td>{row.last_success_at ? age(row.last_success_at) : t("calendarDebug.never")}</td>
              </tr>
            ))}
            {!data?.seasons.length && (
              <tr><td colSpan={4} className="muted">{t("calendarDebug.noSeasons")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 3. Sources */}
      <h3>{t("calendarDebug.sources")}</h3>
      <div className="table-scroll">
        <table className="sources-table">
          <thead>
            <tr>
              <th>{t("calendarDebug.colSource")}</th>
              <th>{t("calendarDebug.colLastOk")}</th>
              <th>{t("calendarDebug.colFailures")}</th>
              <th>{t("calendarDebug.colError")}</th>
            </tr>
          </thead>
          <tbody>
            {(data?.sources ?? []).map((row) => (
              <tr key={row.provider}>
                <td>{row.provider}</td>
                <td>{row.last_success_at ? age(row.last_success_at) : t("calendarDebug.never")}</td>
                <td>{row.failures || "—"}</td>
                <td className="muted">{row.last_error ?? "—"}</td>
              </tr>
            ))}
            {!data?.sources.length && (
              <tr><td colSpan={4} className="muted">{t("calendarDebug.noSources")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 4. What just happened */}
      <h3>{t("calendarDebug.trace")}</h3>
      <ul className="trace-list">
        {(data?.events ?? []).map((event, index) => (
          <li key={`${event.at}-${index}`} className={`trace-${event.level}`}>
            <code>{moment(event.at)}</code>
            <strong>{event.stage}</strong>
            <span>{event.message}</span>
            {Object.keys(event.detail).length > 0 && (
              <small className="muted">
                {Object.entries(event.detail).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}
              </small>
            )}
          </li>
        ))}
        {!data?.events.length && <li className="muted">{t("calendarDebug.noEvents")}</li>}
      </ul>
    </section>
  );
}

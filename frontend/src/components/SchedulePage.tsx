/**
 * The Schedule page (build 158): one place to see what runs on its own, and
 * when.
 *
 * Modelled on Sonarr/Radarr's System > Tasks page at the user's request: a
 * flat list, not a calendar - Name, Interval, Last Execution, Next Execution,
 * a manual trigger.
 *
 * Build 161 dropped the click-to-expand row (the scroll-preserving accordion
 * from build 158 - reported as broken, and not worth keeping just to show a
 * description that a hover tooltip says just as well) and added the other
 * half of what a row can do: a gear button next to Run opens an editor for
 * the task's own switch and interval, right here rather than only on the
 * Settings page. Only offered where there is something to edit -
 * `interval_setting` is null for a task with no independent schedule at all.
 * That same build also removed the page's own title/hint line as redundant
 * with the nav heading above it - restored at build 163, since every other
 * page keeps one anyway and Schedule without one read as inconsistent.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  listScheduleTasks, resetScheduleTask, runScheduleTask, updateScheduleTaskSettings,
  type ScheduleTask,
} from "../api/client";
import { composeSeconds, decomposeSeconds, formatDurationWords, formatRelative } from "../duration";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

function RunIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      className={spinning ? "schedule-run-icon spinning" : "schedule-run-icon"}
      viewBox="0 0 24 24" width="15" height="15" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 11A8.1 8.1 0 0 0 4.5 9M4 5v4h4" />
      <path d="M4 13a8.1 8.1 0 0 0 15.5 2M20 19v-4h-4" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function dotClass(task: ScheduleTask): string {
  if (!task.implemented || task.blocked_reason) return "idle";
  if (task.last_error) return "warn";
  if (!task.enabled) return "idle";
  return "healthy";
}

/** What the dot means, as a hover title - the dot alone was confusing
 *  ("what exactly does this mean?"). */
function dotTitle(task: ScheduleTask): string {
  if (!task.implemented) return t("schedule.dot.notBuilt");
  if (task.blocked_reason) return t(`schedule.blocked.${task.blocked_reason}`);
  if (task.last_error) return t("schedule.dot.error");
  if (!task.enabled) return t("schedule.dot.disabled");
  return t("schedule.dot.healthy");
}

/**
 * Editing one task's own switch and interval (build 161): opened by the gear
 * button, never shown for a task whose `interval_setting` is null - there is
 * nothing here to turn on a schedule for a task that structurally cannot
 * have one (Shoko adoption, filling in details, and the like are manual by
 * design, not by a switch this dialog could flip).
 */
function TaskEditDialog({
  task, onClose, onSaved,
}: {
  task: ScheduleTask;
  onClose: () => void;
  onSaved: (task: ScheduleTask) => void;
}) {
  const initial = decomposeSeconds(task.interval_seconds ?? 3600);
  const [auto, setAuto] = useState(task.enabled);
  const [value, setValue] = useState(initial.value);
  const [unit, setUnit] = useState(initial.unit);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useLockedPageScroll();

  async function save() {
    if (!task.interval_setting) return;
    setBusy("save");
    setError(null);
    try {
      const seconds = composeSeconds(Math.max(1, value), unit);
      const updates: Record<string, boolean | number> = {
        [task.interval_setting]: task.interval_unit === "minutes" ? Math.round(seconds / 60) : seconds,
      };
      if (task.enabled_setting) updates[task.enabled_setting] = auto;
      await updateScheduleTaskSettings(updates);
      const refreshed = (await listScheduleTasks()).find((row) => row.id === task.id);
      if (refreshed) onSaved(refreshed);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  }

  async function reset() {
    setBusy("reset");
    setError(null);
    try {
      onSaved(await resetScheduleTask(task.id));
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <section className="dialog schedule-edit">
        <div className="dialog-head">
          <h2>{t(`schedule.tasks.${task.id}.name`)}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </div>

        <div className="schedule-edit-mode">
          <label className="check">
            <input type="radio" name="schedule-edit-mode" checked={auto} onChange={() => setAuto(true)} />
            <span>{t("schedule.edit.auto")}</span>
          </label>
          <label className="check">
            <input type="radio" name="schedule-edit-mode" checked={!auto} onChange={() => setAuto(false)} />
            <span>{t("schedule.edit.manual")}</span>
          </label>
        </div>

        <label>
          <span>{t("schedule.edit.interval")}</span>
          <div className="schedule-edit-interval-row">
            <input
              type="number" min={1} value={value}
              onChange={(event) => setValue(Math.max(1, Number(event.target.value) || 1))}
            />
            <select value={unit} onChange={(event) => setUnit(event.target.value)}>
              <option value="m">{t("schedule.edit.unit.m")}</option>
              <option value="h">{t("schedule.edit.unit.h")}</option>
              <option value="d">{t("schedule.edit.unit.d")}</option>
              <option value="w">{t("schedule.edit.unit.w")}</option>
              <option value="M">{t("schedule.edit.unit.M")}</option>
            </select>
          </div>
        </label>

        {error && <p className="error-message">{error}</p>}

        <div className="action-stack horizontal">
          <button className="button primary" disabled={busy !== null} onClick={() => void save()}>
            {busy === "save" ? t("schedule.edit.saving") : t("schedule.edit.save")}
          </button>
          <button className="button secondary" disabled={busy !== null} onClick={() => void reset()}>
            {busy === "reset" ? t("schedule.edit.resetting") : t("schedule.edit.reset")}
          </button>
          <button className="button secondary" disabled={busy !== null} onClick={onClose}>
            {t("schedule.edit.close")}
          </button>
        </div>
      </section>
    </div>
  );
}

export function SchedulePage() {
  const [tasks, setTasks] = useState<ScheduleTask[] | null>(null);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [resettingAll, setResettingAll] = useState(false);
  const pollRef = useRef<number | null>(null);

  function startPolling() {
    if (pollRef.current !== null) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const current = await listScheduleTasks();
        setTasks(current);
        if (!current.some((task) => task.running || task.requested)) {
          if (pollRef.current !== null) window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        /* keep polling; transient API hiccups are fine */
      }
    }, 2000);
  }

  const load = useCallback(async () => {
    try {
      const current = await listScheduleTasks();
      setTasks(current);
      setError(false);
      if (current.some((task) => task.running || task.requested)) startPolling();
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [load]);

  async function runTask(id: string) {
    setPending(id);
    try {
      const result = await runScheduleTask(id);
      if (result.started) {
        await load();
        startPolling();
      }
    } catch {
      setError(true);
    } finally {
      setPending(null);
    }
  }

  function applyUpdated(updated: ScheduleTask) {
    setTasks((current) => current?.map((row) => (row.id === updated.id ? updated : row)) ?? current);
  }

  /** Every schedulable task's own switch and interval, back to the
   *  environment's default, in one confirmed action (build 162) - so
   *  starting over does not mean opening each editor one at a time.
   *
   *  One at a time on purpose, not `Promise.all`: this writes the same
   *  settings file every other save does, and nine concurrent read-modify-
   *  writes to it raced each other - three of nine came back 500 the first
   *  time this ran for real. The backend now locks around a save too, but
   *  there is still no reason for nine requests to arrive at once for what
   *  is, to the person who pressed the button, a single action. */
  async function resetAllToDefault() {
    if (!tasks || !window.confirm(t("schedule.resetAllConfirm"))) return;
    setResettingAll(true);
    setError(false);
    try {
      const eligible = tasks.filter((task) => task.interval_setting !== null);
      for (const task of eligible) {
        const updated = await resetScheduleTask(task.id);
        applyUpdated(updated);
      }
    } catch {
      setError(true);
    } finally {
      setResettingAll(false);
    }
  }

  // A-Z by the visible name, not the registry's execution order (build 158).
  // The name is always "Category: what it's doing", so sorting the text
  // groups every category's tasks together as a side effect - Downloads
  // next to Downloads, Watchlist next to Watchlist - without needing a
  // second, separate sort key. The backend keeps its own declared order
  // for the tick loop, where it decides execution sequencing; this is
  // purely how the page displays the same list.
  const sortedTasks = tasks
    ? [...tasks].sort((a, b) =>
        t(`schedule.tasks.${a.id}.name`).localeCompare(t(`schedule.tasks.${b.id}.name`)),
      )
    : null;

  const editingTask = editing ? sortedTasks?.find((row) => row.id === editing) ?? null : null;
  const anyResettable = sortedTasks?.some((task) => task.interval_setting !== null) ?? false;

  return (
    <section className="schedule-page">
      <div className="detail-panel">
        {/* The page's own title/hint line, restored at build 163 - every
            other page has one (Activity's is the closest match: an <h2>
            plus its action button in one row, the hint below it). Removed
            at build 161 as redundant with the nav heading above it; brought
            back because every *other* page keeps one anyway, so leaving
            Schedule without one read as inconsistent rather than tidier. The
            bulk reset button lives in this row now instead of its own -
            same place Activity's "Clear" sits, beside its own title. */}
        <div className="status-line">
          <h2>{t("schedule.title")}</h2>
          {/* One button for every schedulable task at once (build 162) - the
              same reset each gear button already offers, so starting over
              does not mean opening each editor in turn. */}
          {anyResettable && (
            <button
              className="button secondary"
              disabled={resettingAll}
              onClick={() => void resetAllToDefault()}
            >
              {resettingAll ? t("schedule.edit.resetting") : t("schedule.resetAll")}
            </button>
          )}
        </div>
        <p className="muted" style={{ margin: "10px 0 16px" }}>{t("schedule.hint")}</p>
        {error && <p className="error-message">{t("schedule.error")}</p>}
        {sortedTasks && (
          <div className="schedule-table-wrap">
            <table className="schedule-table">
              <thead>
                <tr>
                  <th>{t("schedule.column.name")}</th>
                  <th>{t("schedule.column.interval")}</th>
                  <th>{t("schedule.column.last")}</th>
                  <th>{t("schedule.column.next")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sortedTasks.map((task) => {
                  const busy = task.running || task.requested || pending === task.id;
                  return (
                    <tr
                      key={task.id}
                      className={task.implemented ? "schedule-row" : "schedule-row disabled"}
                    >
                      <td title={t(`schedule.tasks.${task.id}.description`)}>
                        <span className="schedule-name">
                          <span className={`health-dot ${dotClass(task)}`} title={dotTitle(task)} />
                          {t(`schedule.tasks.${task.id}.name`)}
                        </span>
                      </td>
                      <td>
                        {!task.implemented || task.blocked_reason
                          ? "—"
                          : task.interval_seconds !== null
                            ? formatDurationWords(task.interval_seconds)
                            : t("schedule.manualOnly")}
                      </td>
                      <td>{task.implemented && !task.blocked_reason ? (formatRelative(task.last_run_at) || t("schedule.never")) : "—"}</td>
                      <td>
                        {!task.implemented
                          ? t("schedule.notBuilt")
                          : task.blocked_reason
                            ? t(`schedule.blocked.${task.blocked_reason}`)
                            : task.running
                              ? t("schedule.running")
                              : formatRelative(task.next_run_at) || "—"}
                      </td>
                      <td className="schedule-run-cell">
                        {/* Two fixed slots, gear then run (build 162, at the
                            user's request): a button present only sometimes
                            must not shift the other one sideways depending on
                            whether its neighbour is there - each type of
                            button always lands in its own column. The flex
                            layout lives on this inner span, not the <td>
                            itself (build 163): a table cell set to
                            `display: flex` stops sharing the row's height
                            with its siblings, which is what the "black line"
                            above a wrapped row's name actually was - every
                            other cell stayed at the row's true height while
                            this one shrank to its own content. */}
                        <span className="schedule-run-cell-inner">
                          <span className="schedule-icon-slot">
                            {task.implemented && task.interval_setting !== null && (
                              <button
                                className="button secondary schedule-gear"
                                title={t("schedule.edit.open")}
                                onClick={() => setEditing(task.id)}
                              >
                                <GearIcon />
                              </button>
                            )}
                          </span>
                          <span className="schedule-icon-slot">
                            {task.implemented && task.manual && (
                              <button
                                className="button secondary schedule-run"
                                disabled={busy}
                                title={t("schedule.run")}
                                onClick={() => void runTask(task.id)}
                              >
                                <RunIcon spinning={busy} />
                              </button>
                            )}
                          </span>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editingTask && (
        <TaskEditDialog
          task={editingTask}
          onClose={() => setEditing(null)}
          onSaved={applyUpdated}
        />
      )}
    </section>
  );
}

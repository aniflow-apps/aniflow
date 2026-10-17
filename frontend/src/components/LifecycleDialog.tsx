import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

export interface LifecyclePayload {
  lifecycle: string;
  drop_mode?: string;
}

interface Option {
  key: string;
  label: string;
  payload: LifecyclePayload;
}

/** The six things an anime's status can be set to. Upcoming (build 207) is for a show that has not started. */
export const LIFECYCLE_OPTIONS: Option[] = [
  { key: "upcoming", label: "detail.upcoming", payload: { lifecycle: "UPCOMING" } },
  { key: "active", label: "detail.activate", payload: { lifecycle: "ACTIVE" } },
  { key: "complete", label: "detail.complete", payload: { lifecycle: "COMPLETE" } },
  { key: "dropStop", label: "detail.dropStop", payload: { lifecycle: "DROPPED", drop_mode: "STOP" } },
  { key: "dropFinish", label: "detail.dropFinish", payload: { lifecycle: "DROPPED", drop_mode: "FINISH_EXISTING" } },
  { key: "archive", label: "detail.archive", payload: { lifecycle: "ARCHIVED" } },
];

/**
 * Which of the options the anime is in now. A drop with no recorded mode is read as "stop", the stricter of
 * the two, which is also what it behaved as before the mode existed.
 */
export function currentOption(lifecycle: string, dropMode: string | null): string | null {
  if (lifecycle === "UPCOMING") return "upcoming";
  if (lifecycle === "ACTIVE") return "active";
  if (lifecycle === "COMPLETE") return "complete";
  if (lifecycle === "ARCHIVED") return "archive";
  if (lifecycle === "DROPPED") return dropMode === "FINISH_EXISTING" ? "dropFinish" : "dropStop";
  return null;
}

/** "Active", or "Dropped - finishing existing": the label the panel shows. */
export function currentStatusText(lifecycle: string, dropMode: string | null): string {
  const base = t(`status.${lifecycle}`);
  if (lifecycle !== "DROPPED") return base;
  return `${base} - ${t(dropMode === "FINISH_EXISTING" ? "detail.dropFinish" : "detail.dropStop")}`;
}

/**
 * Pick what happens to an anime from now on (build 188).
 *
 * Five buttons used to sit in the detail page's side column all the time and
 * took a third of it. Now the column says what the status is and offers one
 * Edit button; this is where the choice is made. The current status is blue
 * and cannot be pressed, so the dialog says where things stand as well as
 * letting you change it. Choosing one does it and closes.
 */
export function LifecycleDialog({
  lifecycle,
  dropMode,
  busy,
  onChoose,
  onClose,
}: {
  lifecycle: string;
  dropMode: string | null;
  busy: boolean;
  onChoose: (payload: LifecyclePayload) => void;
  onClose: () => void;
}) {
  useLockedPageScroll();
  const current = currentOption(lifecycle, dropMode);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog lifecycle-dialog">
        <header className="dialog-head">
          <h2>{t("detail.lifecycleTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>
            ×
          </button>
        </header>
        <p className="muted">{t("detail.lifecycleHint")}</p>
        <div className="action-stack lifecycle-options">
          {LIFECYCLE_OPTIONS.map((option) => {
            const isCurrent = option.key === current;
            return (
              <button
                key={option.key}
                className={isCurrent ? "button primary lifecycle-option current" : "button secondary lifecycle-option"}
                aria-pressed={isCurrent}
                disabled={busy && !isCurrent}
                onClick={() => {
                  if (!isCurrent) onChoose(option.payload);
                }}
              >
                {t(option.label)}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

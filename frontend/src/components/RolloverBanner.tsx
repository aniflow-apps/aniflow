import { useCallback, useEffect, useState } from "react";
import {
  getRollover,
  listLibraries,
  moveToLibrary,
  pinAnimeInLibrary,
  type Library,
  type Rollover,
  type RolloverGroup,
} from "../api/client";
import { t } from "../i18n";
import { rolloverCount } from "../rollover";
import { keepIfSame } from "../sameJson";
import { useToast } from "../toasts";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { libraryName } from "./LibraryMoveDialog";

const LATER_KEY = "aniflow.rollover.later";

/* The last answer, so the banner is already there when the watchlist opens again and the page does
   not move down a moment later (build 207). Replaced only when the answer differs. */
let lastAnswer: Rollover | null = null;

/**
 * The season check on the watchlist (build 205).
 *
 * Shown only while there is something to decide (the setting is "ask" and some
 * anime are not in the library that matches what they are doing). "Not now"
 * hides it until the page is opened again. Nothing here moves anything: the
 * Review window does, and only what is ticked.
 */
export function RolloverBanner({ onChanged }: { onChanged: () => void }) {
  const [rollover, setRollover] = useState<Rollover | null>(lastAnswer);
  const [reviewing, setReviewing] = useState(false);
  const [later, setLater] = useState(() => {
    try {
      return window.sessionStorage.getItem(LATER_KEY) === "1";
    } catch {
      return false;
    }
  });

  const load = useCallback(() => {
    void getRollover()
      .then((answer) => {
        lastAnswer = answer;
        setRollover((current) => keepIfSame(current, answer));
      })
      .catch(() => undefined);
  }, []);

  useEffect(load, [load]);

  if (!rollover) return null;
  const total = rolloverCount(rollover);
  if (total === 0) return null;
  if (later && !reviewing) return null;

  const parts = [
    rollover.earlier.items.length > 0 ? t("rollover.partEarlier").replace("{n}", String(rollover.earlier.items.length)) : null,
    rollover.airing.items.length > 0 ? t("rollover.partAiring").replace("{n}", String(rollover.airing.items.length)) : null,
  ].filter(Boolean);

  return (
    <>
      {!later && (
        <div className="rollover-banner" role="status">
          <span>
            <strong>{t("rollover.title")}</strong> {t("rollover.bannerText").replace("{parts}", parts.join(" · "))}
          </span>
          <span className="rollover-banner-actions">
            <button className="button primary" onClick={() => setReviewing(true)}>
              {t("rollover.review")}
            </button>
            <button
              className="button secondary"
              onClick={() => {
                try {
                  window.sessionStorage.setItem(LATER_KEY, "1");
                } catch {
                  // Storage blocked: it comes back next time, which is fine.
                }
                setLater(true);
              }}
            >
              {t("rollover.later")}
            </button>
          </span>
        </div>
      )}
      {reviewing && (
        <RolloverDialog
          rollover={rollover}
          onClose={() => setReviewing(false)}
          onChanged={() => {
            load();
            onChanged();
          }}
        />
      )}
    </>
  );
}

/** The review window: two groups, each with a target library, a tick per anime and "Keep here". */
export function RolloverDialog({
  rollover,
  onClose,
  onChanged,
}: {
  rollover: Rollover;
  onClose: () => void;
  onChanged: () => void;
}) {
  useLockedPageScroll();
  const [libraries, setLibraries] = useState<Library[]>([]);

  useEffect(() => {
    void listLibraries(true)
      .then((answer) => setLibraries(answer.libraries.filter((library) => library.has_folder)))
      .catch(() => undefined);
  }, []);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide">
        <header className="dialog-head">
          <h2>{t("rollover.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("rollover.hint")}</p>
        {rollover.earlier.items.length > 0 && (
          <RolloverGroupView
            heading={t("rollover.groupEarlier")}
            group={rollover.earlier}
            libraries={libraries.filter((library) => library.fits_season !== "current")}
            onChanged={onChanged}
          />
        )}
        {rollover.airing.items.length > 0 && (
          <RolloverGroupView
            heading={t("rollover.groupAiring")}
            group={rollover.airing}
            libraries={libraries}
            onChanged={onChanged}
          />
        )}
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

function RolloverGroupView({
  heading,
  group,
  libraries,
  onChanged,
}: {
  heading: string;
  group: RolloverGroup;
  libraries: Library[];
  onChanged: () => void;
}) {
  const { show } = useToast();
  const [target, setTarget] = useState<string>(group.target_id?.toString() ?? "");
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [ticked, setTicked] = useState<Set<string>>(() => new Set(group.items.map((item) => item.anime_id)));
  const [busy, setBusy] = useState(false);
  const items = group.items.filter((item) => !gone.has(item.anime_id));

  function toggle(id: string) {
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function keep(id: string) {
    try {
      await pinAnimeInLibrary([id], true);
      setGone((current) => new Set(current).add(id));
      onChanged();
    } catch (failure) {
      show(failure instanceof Error ? failure.message : String(failure));
    }
  }

  async function move() {
    const ids = items.filter((item) => ticked.has(item.anime_id)).map((item) => item.anime_id);
    if (ids.length === 0 || !target) return;
    setBusy(true);
    try {
      const results = await moveToLibrary(ids, Number(target), true);
      const moved = results.filter((r) => r.outcome === "moved" || r.outcome === "reassigned").map((r) => r.anime_id);
      const refused = results.filter((r) => r.outcome === "refused");
      setGone((current) => new Set([...current, ...moved]));
      show(t("rollover.moved").replace("{n}", String(moved.length)));
      if (refused.length > 0) {
        show(t("rollover.refused").replace("{n}", String(refused.length)).replace("{why}", refused[0].detail ?? refused[0].name));
      }
      onChanged();
    } catch (failure) {
      show(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  if (items.length === 0) return null;
  return (
    <section className="rollover-group">
      <div className="rollover-group-head">
        <h3>{heading}</h3>
        <select value={target} onChange={(event) => setTarget(event.target.value)} aria-label={heading}>
          <option value="">{t("rollover.chooseTarget")}</option>
          {libraries.map((library) => (
            <option key={library.id} value={String(library.id)}>
              {libraryName(library)}
            </option>
          ))}
        </select>
      </div>
      <ul className="library-move-list">
        {items.map((item) => (
          <li key={item.anime_id} className="library-move-row">
            <label className="check">
              <input type="checkbox" checked={ticked.has(item.anime_id)} onChange={() => toggle(item.anime_id)} />
              {item.name}
            </label>
            <span className="chip sev-notice rollover-reason">{t(`rollover.reason.${item.reason}`)}</span>
            <button className="button secondary library-row-move" onClick={() => void keep(item.anime_id)}>
              {t("rollover.keep")}
            </button>
          </li>
        ))}
      </ul>
      <div className="dialog-actions">
        <button
          className="button primary"
          disabled={busy || !target || !items.some((item) => ticked.has(item.anime_id))}
          onClick={() => void move()}
        >
          {t("rollover.move")}
        </button>
      </div>
    </section>
  );
}

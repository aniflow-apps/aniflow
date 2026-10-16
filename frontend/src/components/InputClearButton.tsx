import { useEffect, useRef, useState } from "react";
import { clearField, factsOf, wantsClear } from "../inputClear";
import { t } from "../i18n";

interface Spot {
  top: number;
  left: number;
  height: number;
}

/**
 * The × inside whichever text field is under the pointer or has the focus (build 210).
 *
 * Mounted once. It follows the field with its own animation frame while one is showing - cheap, and
 * the only way to stay put inside dialogs that scroll, resize and re-lay-out under it. It never
 * takes the focus: pressing it clears the field and leaves the cursor there.
 */
export function InputClearButton() {
  const [spot, setSpot] = useState<Spot | null>(null);
  const target = useRef<HTMLInputElement | null>(null);
  const hovered = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const pick = (element: EventTarget | null): HTMLInputElement | null =>
      element instanceof HTMLInputElement ? element : null;

    const onOver = (event: MouseEvent) => {
      const over = pick(event.target);
      if (over) hovered.current = over;
      else if (!(event.target instanceof HTMLElement && event.target.closest(".input-clear-button"))) hovered.current = null;
    };
    document.addEventListener("mouseover", onOver);

    let frame = 0;
    const follow = () => {
      const focused = pick(document.activeElement);
      const candidate = [focused, hovered.current].find((input) => input !== null && wantsClear(factsOf(input))) ?? null;
      target.current = candidate;
      if (!candidate) {
        setSpot((current) => (current === null ? current : null));
      } else {
        const box = candidate.getBoundingClientRect();
        const next = { top: Math.round(box.top), left: Math.round(box.right - 28), height: Math.round(box.height) };
        setSpot((current) =>
          current && current.top === next.top && current.left === next.left && current.height === next.height ? current : next,
        );
      }
      frame = window.requestAnimationFrame(follow);
    };
    frame = window.requestAnimationFrame(follow);

    return () => {
      document.removeEventListener("mouseover", onOver);
      window.cancelAnimationFrame(frame);
    };
  }, []);

  if (!spot) return null;
  return (
    <button
      type="button"
      className="input-clear-button"
      tabIndex={-1}
      aria-label={t("setup.clearFilter")}
      title={t("setup.clearFilter")}
      style={{ top: spot.top, left: spot.left, height: spot.height }}
      // The field keeps the focus: taking it would blur the field and move the pointer's target.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        if (target.current) clearField(target.current);
      }}
    >
      ×
    </button>
  );
}

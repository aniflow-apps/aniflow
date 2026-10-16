/**
 * The two corners on a watchlist poster (build 151).
 *
 * Asked for with a mockup: *"flipped corners ... I particularly like that the
 * two indicators have their own territory. They don't fight for space, even
 * when both are present."* And one refinement on top of it: both corners a
 * little more subtle than the mockup, and of the same visual weight.
 *
 * The blue corner only informs, so it is a label with a tooltip. The red one
 * opens a list of every group and what is wrong with it - *"all the issues
 * listed in one go"* - so it is a real button, and the card around it is not
 * a <button> any more (a button inside a button is not valid HTML, and
 * browsers disagree about which one gets the click).
 *
 * The list is drawn into <body>: the card clips its content to its rounded
 * corners, and a bubble that has to escape the card cannot live inside it.
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { t } from "../i18n";
import {
  GROUPS,
  attentionLevel,
  attentionTooltip,
  groupDetail,
  newEpisodeLabel,
  newEpisodeTooltip,
  problemGroups,
  type CornerHealth,
} from "../cardCorners";

export function NewEpisodeCorner({ count, newestAt }: { count?: number; newestAt?: string | null }) {
  const label = newEpisodeLabel(count);
  if (!label || !count) return null;
  const title = newEpisodeTooltip(count, newestAt);
  return (
    <span className="card-corner card-corner-new" title={title} aria-label={title} role="img">
      {label}
    </span>
  );
}

/** Width of the bubble, and the gap between it and the card. */
const BUBBLE_WIDTH = 280;
const GAP = 10;

export function AttentionCorner({ health, onOpen }: { health: CornerHealth; onOpen: () => void }) {
  const level = attentionLevel(health);
  const [open, setOpen] = useState(false);
  const corner = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<CSSProperties>({});

  /* Beside the card rather than over it, like the mockup: to the right when
     there is room, to the left when the card is in the last column. */
  useLayoutEffect(() => {
    if (!open) return;
    const card = corner.current?.closest(".anime-card") ?? corner.current;
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const right = rect.right + GAP + BUBBLE_WIDTH <= window.innerWidth - 8;
    const left = right ? rect.right + GAP : Math.max(8, rect.left - GAP - BUBBLE_WIDTH);
    const height = bubble.current?.offsetHeight ?? 0;
    const top = Math.max(8, Math.min(rect.top, window.innerHeight - height - 8));
    setPlace({ left, top, width: BUBBLE_WIDTH });
  }, [open]);

  /* Closes on a click elsewhere, on Escape, and when the page moves - a
     bubble pinned to where a card used to be is worse than no bubble. */
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (bubble.current?.contains(target) || corner.current?.contains(target)) return;
      setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const moved = () => setOpen(false);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("scroll", moved, true);
    window.addEventListener("resize", moved);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("scroll", moved, true);
      window.removeEventListener("resize", moved);
    };
  }, [open]);

  if (!level) return null;
  const problems = problemGroups(health);
  const title = attentionTooltip(health);

  return (
    <>
      <button
        ref={corner}
        type="button"
        className={`card-corner card-corner-attention ${level}`}
        title={open ? undefined : title}
        aria-label={title}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => event.stopPropagation()}
      >
        !
      </button>
      {open && createPortal(
        <div
          ref={bubble}
          className="attention-bubble"
          style={place}
          role="dialog"
          aria-label={t("card.attention.bubbleTitle")}
          onClick={(event) => event.stopPropagation()}
        >
          <strong className="attention-bubble-title">{t("card.attention.bubbleTitle")}</strong>
          <small className="attention-bubble-count">
            {problems.length === 1
              ? t("card.attention.countOne")
              : t("card.attention.countMany").replace("{n}", String(problems.length))}
          </small>
          <ul className="attention-bubble-list">
            {GROUPS.map((group) => {
              const state = health?.[group];
              const colour = state?.colour ?? "grey";
              return (
                <li key={group} className={`attention-bubble-row ${colour}`}>
                  <span className="attention-bubble-mark" aria-hidden="true">
                    {colour === "red" || colour === "yellow" ? "!" : colour === "green" ? "✓" : "–"}
                  </span>
                  <span>
                    <strong>{t(`health.group.${group}`)}</strong>
                    <small>{groupDetail(state)}</small>
                  </span>
                </li>
              );
            })}
          </ul>
          <button
            type="button"
            className="button secondary attention-bubble-open"
            onClick={() => {
              setOpen(false);
              onOpen();
            }}
          >
            {t("card.attention.open")}
          </button>
        </div>,
        document.body,
      )}
    </>
  );
}

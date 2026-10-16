import { t } from "../i18n";

/** The four kinds of title a provider gives us, in the order stored. */
export const TITLE_KINDS = ["english", "romaji", "alternative", "japanese"] as const;

/**
 * An ordered preference list: top is the first choice.
 *
 * Buttons rather than drag-and-drop. There are four items and they move one
 * step at a time, so a pair of arrows is quicker, works by keyboard, and has
 * no hidden state - dragging would be a lot of machinery to reorder four rows.
 */
export function TitleOrder({
  value,
  onChange,
}: {
  /** Comma-separated, most-wanted first. Unknown names are ignored. */
  value: string;
  onChange: (next: string) => void;
}) {
  const parsed = value
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => (TITLE_KINDS as readonly string[]).includes(item));
  // Anything left out is still shown, at the bottom, because it is still used
  // as a fallback - hiding it would misrepresent what happens.
  const order = [...parsed, ...TITLE_KINDS.filter((kind) => !parsed.includes(kind))];

  function move(index: number, by: -1 | 1) {
    const next = [...order];
    const target = index + by;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next.join(","));
  }

  return (
    <ol className="title-order">
      {order.map((kind, index) => (
        <li key={kind}>
          <span className="title-order-rank">{index + 1}</span>
          <span className="title-order-name">{t(`settings.title.${kind}`)}</span>
          <span className="title-order-moves">
            <button
              type="button"
              className="button secondary"
              disabled={index === 0}
              aria-label={t("settings.titleMoveUp")}
              onClick={() => move(index, -1)}
            >
              ↑
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={index === order.length - 1}
              aria-label={t("settings.titleMoveDown")}
              onClick={() => move(index, 1)}
            >
              ↓
            </button>
          </span>
        </li>
      ))}
    </ol>
  );
}

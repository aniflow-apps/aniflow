import type { AnimeSearchResult } from "../api/client";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { t } from "../i18n";
import { Cover } from "./Cover";
import { airsOn, hasAnidbId, kindLabel, starText, statusLabel } from "../calendarView";
import { formatDate } from "../formatWhen";

/**
 * Indexed by `Date.getDay()`, which counts from Sunday - so the list starts
 * there even though every other view in AniFlow starts on Monday.
 */
const WEEKDAYS = [
  "dashboard.weekday.sun",
  "dashboard.weekday.mon",
  "dashboard.weekday.tue",
  "dashboard.weekday.wed",
  "dashboard.weekday.thu",
  "dashboard.weekday.fri",
  "dashboard.weekday.sat",
];

/**
 * Everything one calendar entry knows (build 101).
 *
 * Asked for: clicking a card anywhere - not only the picture - opens this,
 * with a bigger cover and the things a season card has no room for. The card
 * shows three genres and the name; there are usually eight genres, a studio,
 * a weekday and a synopsis behind it.
 *
 * The Add button is deliberately not part of the click target, on the card or
 * here: opening a page is a free action and adding an anime is not, and one
 * should never be a miss-click away from the other.
 *
 * Everything shown comes from what was already downloaded - this opens with no
 * request at all, which is what makes it worth opening on a single click.
 */
export function CalendarDetailDialog({
  item,
  stars,
  related,
  onAdd,
  onClose,
}: {
  item: AnimeSearchResult;
  /** One to five, relative to the season it was shown in. Zero for unknown. */
  stars: number;
  /** How it relates to the seasons already tracked, when that is being shown. */
  related?: string | null;
  onAdd?: (item: AnimeSearchResult) => void;
  onClose: () => void;
}) {
  useLockedPageScroll();
  const slot = airsOn(item);
  // Always shown here when a source published one, unlike on the card - see
  // `worthShowing` for why the card is choosier.
  const status = statusLabel(item.airing_status);
  const titles = [item.title_english, item.title_romaji, item.title_native]
    .filter((name): name is string => Boolean(name) && name !== item.display_name);

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <section
        className="dialog wide calendar-detail"
        onClick={(event) => event.stopPropagation()}
      >
        {/* A dialog head, not a status line (build 124). Reported as
            *"the x on the anime calendar if you click on a card is on the
            left side"* - and it was the same cause as the Debug window a
            build earlier: `.status-line` groups things that belong together,
            which is exactly what puts a close button against a title. */}
        <div className="dialog-head">
          <h2>{item.display_name}</h2>
          <button className="icon-button" onClick={onClose} aria-label={t("add.cancel")}>✕</button>
        </div>

        <div className="calendar-detail-body">
          {/* The poster at a fixed 318x450 and no longer a button.
              *"its a bit stupid to have 1 window open, and then have another
              window open on top. So we will remove this feature of zooming in
              but will make the picture itself bigger."* - so the dialog is
              wider by exactly the poster's extra width and the text column is
              untouched. */}
          <div className="calendar-detail-art">
            <Cover anime={item} />
            {stars > 0 && (
              <div className="calendar-stars" title={t("calendar.starsHint")}>
                {starText(stars)}
              </div>
            )}
          </div>

          <div className="calendar-detail-facts">
            {titles.length > 0 && <p className="muted">{titles.join(" · ")}</p>}

            <dl className="fact-list">
              <dt>{t("calendar.fact.kind")}</dt>
              <dd>{kindLabel(item.format) ?? "—"}</dd>

              <dt>{t("calendar.fact.episodes")}</dt>
              <dd>{item.episode_count ?? "—"}</dd>

              {/* Always here, unlike on the card: there is one of these and
                  it has the room, and "is this over" is exactly the kind of
                  thing somebody opens a detail view to find out. */}
              {status && (
                <>
                  <dt>{t("calendar.fact.status")}</dt>
                  <dd title={t("calendar.statusHint")}>{status}</dd>
                </>
              )}

              <dt>{t("calendar.fact.airs")}</dt>
              <dd>
                {/* The weekday and the time when a source published one; the
                    premiere date otherwise. Two different facts, and saying
                    which is which matters - a weekly slot is a schedule and a
                    start date is not. */}
                {/* The weekday always, the time when a source published
                    one. Both rows appear whenever there is anything to put in
                    them, so two entries never describe the same facts in two
                    different shapes. */}
                {slot
                  ? slot.time
                    ? `${t(WEEKDAYS[slot.weekday])} · ${slot.time}`
                    : t(WEEKDAYS[slot.weekday])
                  : "—"}
              </dd>

              {item.air_start_date && (
                <>
                  <dt>{t("calendar.fact.premiere")}</dt>
                  <dd>{formatDate(item.air_start_date)}</dd>
                </>
              )}

              {item.studios.length > 0 && (
                <>
                  <dt>{t("calendar.fact.studio")}</dt>
                  <dd>{item.studios.join(", ")}</dd>
                </>
              )}

              {related && (
                <>
                  {/* Why the card was outlined, spelled out - a season of
                      something tracked, or the one between two that are. */}
                  <dt>{t("calendar.fact.related")}</dt>
                  <dd>{t(`calendar.related.${related}`)}</dd>
                </>
              )}

              <dt>{t("calendar.fact.anidb")}</dt>
              <dd>
                {/* Whether Shoko and AniFlow can recognise it by an id rather
                    than by its name, which decides how reliably it can be
                    adopted and matched later. */}
                {hasAnidbId(item)
                  ? `AniDB ${item.provider_ids.anidb}`
                  : t("calendar.fact.noAnidb")}
              </dd>
            </dl>

            {/* Every genre, which is the card's main omission: it shows three. */}
            {item.genres.length > 0 && (
              <p className="calendar-detail-genres">
                {item.genres.map((genre) => (
                  <span className="chip" key={genre}>{genre}</span>
                ))}
              </p>
            )}

            {item.synopsis && <p className="calendar-synopsis">{item.synopsis}</p>}
            {!item.synopsis && (
              <p className="muted">{t("calendar.fact.noSynopsis")}</p>
            )}
          </div>
        </div>

        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>{t("scan.close")}</button>
          {onAdd && !item.in_library && (
            <button className="button primary" onClick={() => onAdd(item)}>
              {t("calendar.addToWatchlist")}
            </button>
          )}
          {item.in_library && <span className="in-library-tag">✓ {t("add.inLibrary")}</span>}
        </div>
      </section>
    </div>
  );
}

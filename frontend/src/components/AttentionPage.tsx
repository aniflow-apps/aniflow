/**
 * Things waiting on a decision (build 125).
 *
 * Asked for: *"currently Attention does not exist as seperate window, we
 * should add it."*  It shared the Activity page, and the two are not the same
 * kind of thing at all:
 *
 * * **Attention** is a to-do list of *currently unresolved* conditions. One
 *   record per subject, refreshed rather than duplicated when it recurs, and
 *   closed automatically once its reason goes away
 *   (services/attention_review.py). An entry here is something to act on.
 * * **Activity** is an append-only diary. Deleting it changes nothing.
 *
 * Interleaving a to-do list with a log is most of why *"its very hard to spot
 * important infos"*. Everything below was moved from ActivityPage rather than
 * rewritten - the eight action blocks and the sentences explaining what each
 * button does are build 100 and 118 work and were already right.
 *
 * What is new is grouping: four episodes that failed for one reason read as
 * one problem. See attentionGroups.ts, which explains why that is a key
 * rather than a clustering algorithm.
 */
import { useCallback, useEffect, useState } from "react";
import {
  acceptMetadataChange,
  approveNewVersion,
  assignAnimeToRelease,
  deleteDownloadedFile,
  dismissAllAttention,
  dismissAttention,
  listAnime,
  listAttention,
  moveFileNow,
  replaceLibraryFile,
  retryDownloadNow,
  type AnimeSummary,
  type AttentionItem,
} from "../api/client";
import { t } from "../i18n";
import { remedyFor, resolvesItself } from "../attentionRemedy";
import { reasonText } from "../reasons";
import { groupAttentions, isCollapsible } from "../attentionGroups";
import { severityClass } from "../severity";
import { DebugSamples } from "./DebugSamples";
import { formatTime } from "../time";

export function AttentionPage({ debugMode = false }: { debugMode?: boolean }) {
  const [attentions, setAttentions] = useState<AttentionItem[]>([]);
  const [animeList, setAnimeList] = useState<AnimeSummary[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [assignFor, setAssignFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAttentions(await listAttention("OPEN"));
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
    /* The anime list is only needed to name the candidates in the
       "this release matches several anime" dropdown. Failing to load it must
       not take the page down with it. */
    void listAnime().then(setAnimeList).catch(() => undefined);
  }, [load]);

  async function act(id: string, action: () => Promise<unknown>) {
    setBusyId(id);
    setError(false);
    try {
      await action();
      await load();
    } catch {
      setError(true);
    } finally {
      setBusyId(null);
    }
  }

  /** Which rows are expanded. Collapsed is the resting state: a list of
   *  problems should read as a list, and open one when it is yours. */
  const [open, setOpen] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /**
   * One record, exactly as it rendered before the split (build 125).
   *
   * A closure rather than a component: it reads `open`, `busyId`, `assignFor`
   * and `animeList` from the page, and lifting those into props would be five
   * parameters to gain nothing. What matters is that grouping did not require
   * touching any of it.
   */
  function renderRow(item: AttentionItem) {
    return (
          <li key={item.id} className={`attention-row ${severityClass(item.kind)}`}>
            {/* One line until asked (build 118).
                Reported as: *"lets keep this window small ... maybe just
                ambiguous release, the title name and the error. Then adds
                a details button"*. The row had everything on it at once -
                episode, release name, files, the remedy, the timestamp and
                four buttons - which made a list of two items fill the
                screen, and buried the one sentence that says what is
                wrong. */}
            {/* Tag, then two capped lines, then the time and the button -
                the same three columns an Activity row uses (build 127):
                *"If you look at the look of the activity, its way cleaner
                like the boxes are same, the tag is on the same pos, color
                vise etc."*  The colour comes from severity.ts rather than
                from the kind's own name, which is why every tag has one now.

                Both lines are clipped to one line each, with the whole text
                on hover and in the details: *"top line for title which is
                capped and bottom line for the 2nd line which is also
                capped."* */}
            <span className={`chip ${severityClass(item.kind)}`}>
              {t(`attention.kind.${item.kind}`)}
            </span>
            <div className="attention-main">
              <strong title={item.anime_name ?? item.feed_name ?? undefined}>
                {item.anime_name ?? item.feed_name ?? "—"}
              </strong>
              <small className="muted" title={item.detail ? reasonText(item.detail) : undefined}>
                {item.detail ? reasonText(item.detail) : t(`attention.summary.${item.kind}`)}
              </small>
            </div>
            <div className="attention-aside">
              <small className="muted">{formatTime(item.created_at)}</small>
              <button
                className="button secondary attention-details-toggle"
                aria-expanded={open.has(item.id)}
                onClick={() => toggle(item.id)}
              >
                {open.has(item.id) ? t("attention.hideDetails") : t("attention.details")}
              </button>
            </div>

            {open.has(item.id) && (
              <div className="attention-body">
                {/* Everything that helps spot what went wrong, which is
                    what the details were asked for: *"which episode, which
                    filename, whatever it can give you"*. */}
                <dl className="attention-facts">
                  {item.episode_number !== null && item.episode_number !== undefined && (
                    <>
                      <dt>{t("profile.episode")}</dt>
                      <dd>{item.episode_number}</dd>
                    </>
                  )}
                  {item.release_label && (
                    <>
                      <dt>{t("attention.releaseName")}</dt>
                      <dd><code>{item.release_label}</code></dd>
                    </>
                  )}
                  {item.episode_label && (
                    <>
                      <dt>{t("attention.episodeLabel")}</dt>
                      <dd><code>{item.episode_label}</code></dd>
                    </>
                  )}
                  {item.feed_name && (
                    <>
                      <dt>{t("attention.feed")}</dt>
                      <dd>{item.feed_name}</dd>
                    </>
                  )}
                  {item.download_file && (
                    <>
                      <dt>{t("attention.fileDownloaded")}</dt>
                      <dd><code>{item.download_file}</code></dd>
                    </>
                  )}
                  {item.library_file && (
                    <>
                      <dt>{t("attention.fileInLibrary")}</dt>
                      <dd><code>{item.library_file}</code></dd>
                    </>
                  )}
                  {item.duplicates > 0 && (
                    <>
                      <dt>{t("attention.duplicatesLabel")}</dt>
                      <dd>{t("attention.duplicates").replace("{n}", String(item.duplicates))}</dd>
                    </>
                  )}
                  <dt>{t("attention.noticed")}</dt>
                  <dd>{formatTime(item.created_at)}</dd>
                </dl>

                {remedyFor(item.kind) && (
                  <p className="attention-fix">
                    {resolvesItself(item.kind) && (
                      <span className="chip resolves">{t("attention.resolvesItself")}</span>
                    )}
                    {t(remedyFor(item.kind)!.message)}
                  </p>
                )}

                {/* What each button will actually do, next to the button
                    that does it: *"its then easier for the user to press a
                    button to know what will happen, in this case i would
                    be scared to press it"*. */}
                <ul className="attention-actions">
                  {item.kind === "AMBIGUOUS_RELEASE" && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.assign")}</strong>
                        <small className="muted">{t("attention.explain.assign")}</small>
                      </div>
                      {assignFor === item.id ? (
                        <select
                          autoFocus
                          onChange={(event) => {
                            const chosen = event.target.value;
                            setAssignFor(null);
                            if (chosen) void act(item.id, () => assignAnimeToRelease(item.id, chosen));
                          }}
                        >
                          <option value="">{t("attention.assignPlaceholder")}</option>
                          {animeList.map((anime) => <option key={anime.id} value={anime.id}>{anime.display_name}</option>)}
                        </select>
                      ) : (
                        <button className="button secondary" disabled={busyId === item.id} onClick={() => setAssignFor(item.id)}>
                          {t("attention.assign")}
                        </button>
                      )}
                    </li>
                  )}
                  {item.kind === "NEW_VERSION" && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.approve")}</strong>
                        <small className="muted">{t("attention.explain.approve")}</small>
                      </div>
                      <button className="button primary" disabled={busyId === item.id}
                              onClick={() => void act(item.id, () => approveNewVersion(item.id))}>
                        {t("attention.approve")}
                      </button>
                    </li>
                  )}
                  {item.kind === "METADATA_CHANGED" && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.acceptMetadata")}</strong>
                        <small className="muted">{t("attention.explain.accept_metadata")}</small>
                      </div>
                      <button className="button primary" disabled={busyId === item.id}
                              onClick={() => void act(item.id, () => acceptMetadataChange(item.id))}>
                        {t("attention.acceptMetadata")}
                      </button>
                    </li>
                  )}
                  {item.actions.includes("retry_now") && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.retryNow")}</strong>
                        <small className="muted">{t("attention.explain.retry_now")}</small>
                      </div>
                      <button className="button primary" disabled={busyId === item.id}
                              onClick={() => void act(item.id, () => retryDownloadNow(item.id))}>
                        {t("attention.retryNow")}
                      </button>
                    </li>
                  )}
                  {item.actions.includes("move_now") && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.moveNow")}</strong>
                        <small className="muted">{t("attention.explain.move_now")}</small>
                      </div>
                      <button className="button primary" disabled={busyId === item.id}
                              onClick={() => void act(item.id, () => moveFileNow(item.id))}>
                        {t("attention.moveNow")}
                      </button>
                    </li>
                  )}
                  {item.actions.includes("delete_download") && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.deleteDownload")}</strong>
                        <small className="muted">{t("attention.explain.delete_download")}</small>
                      </div>
                      <button className="button secondary" disabled={busyId === item.id}
                              onClick={() => void act(item.id, () => deleteDownloadedFile(item.id))}>
                        {t("attention.deleteDownload")}
                      </button>
                    </li>
                  )}
                  {item.actions.includes("replace_library") && (
                    <li>
                      <div className="attention-action-what">
                        <strong>{t("attention.replaceLibrary")}</strong>
                        <small className="muted">{t("attention.explain.replace_library")}</small>
                      </div>
                      <button className="button secondary" disabled={busyId === item.id}
                              onClick={() => {
                                if (!window.confirm(t("attention.replaceLibraryConfirm").replace("{file}", item.library_file ?? ""))) return;
                                void act(item.id, () => replaceLibraryFile(item.id));
                              }}>
                        {t("attention.replaceLibrary")}
                      </button>
                    </li>
                  )}
                  <li>
                    <div className="attention-action-what">
                      <strong>{t("attention.dismiss")}</strong>
                      <small className="muted">{t("attention.explain.dismiss")}</small>
                    </div>
                    <button className="button danger" disabled={busyId === item.id}
                            onClick={() => void act(item.id, () => dismissAttention(item.id))}>
                      {t("attention.dismiss")}
                    </button>
                  </li>
                </ul>
              </div>
            )}
          </li>
    );
  }

  return <section className="activity-page">
  <div className="detail-panel">
    <div className="status-line">
      <h2>{t("activity.attention.title")}</h2>
      {/* One button for a list of things that are all stale - the endpoint
          has existed since build 94 with nothing to press it. */}
      {attentions.length > 0 && (
        <button
          className="button secondary"
          disabled={busyId !== null}
          title={t("attention.dismissAllHint")}
          onClick={() => {
            if (!window.confirm(t("attention.dismissAllConfirm").replace("{n}", String(attentions.length)))) return;
            void act("all", () => dismissAllAttention());
          }}
        >
          {t("attention.dismissAll")}
        </button>
      )}
    </div>
    {error && <p className="error-message">{t("activity.error")}</p>}
    {attentions.length === 0 ? (
      <p className="muted">{t("activity.attention.empty")}</p>
    ) : (
      <ul className="attention-list">
        {groupAttentions(attentions).map((group) =>
          isCollapsible(group) ? (
            /* Several records, one cause. The headline says how much is
               affected and the group opens to the individual rows - which
               are the same rows, with the same buttons. */
            <li key={group.key} className="attention-group">
              {/* The head is a row of exactly the same shape, in the same
                  box (build 127). It was a bare flex line, which made a
                  grouped record look like a different kind of thing:
                  *"can they not be still inside the more dark grey box, so
                  its all consistent?"* */}
              <div className={`attention-row ${severityClass(group.kind)}`}>
                <span className={`chip ${severityClass(group.kind)}`}>
                  {t(`attention.kind.${group.kind}`)}
                </span>
                <div className="attention-main">
                  {/* One anime per group since build 128, so the headline
                      is the episode count and the name is the line below -
                      the same shape every other row has. */}
                  <strong title={group.items[0].anime_name ?? undefined}>
                    {group.items[0].anime_name ?? t("attention.group.noName")}
                  </strong>
                  <small className="muted">
                    {t("attention.group.count").replace("{n}", String(group.items.length))}
                    {group.detail ? ` · ${reasonText(group.detail)}` : ""}
                  </small>
                </div>
                <div className="attention-aside">
                  <button
                    className="button secondary attention-details-toggle"
                    onClick={() => toggle(group.key)}
                  >
                    {open.has(group.key) ? t("attention.group.hide") : t("attention.group.show")}
                  </button>
                </div>
              </div>
              {open.has(group.key) && (
                <ul className="attention-list attention-group-items">
                  {group.items.map((item) => renderRow(item))}
                </ul>
              )}
            </li>
          ) : (
            renderRow(group.items[0])
          ),
        )}
      </ul>
    )}
  </div>


    {debugMode && <DebugSamples onChanged={() => void load()} />}
  </section>;
}

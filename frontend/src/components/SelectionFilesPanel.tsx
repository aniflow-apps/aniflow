import type { ReactNode } from "react";
import type { SelectionScan } from "../api/client";
import type { BatchRow } from "../batchChoice";
import { t } from "../i18n";
import { SeederMark } from "./SeederMark";
import { duplicatesByEpisode } from "../editions";
import {
  episodeRanges,
  episodeRows,
  numbering,
  shortSize,
} from "../selectionView";

/**
 * What the selected row would give you, episode by episode (build 98).
 *
 * Build 97 listed every file the scan looked at, newest first, with a verdict
 * on each - which was a debug view wearing a table. Three things were wrong
 * with it, all reported:
 *
 * * it said "31 files" for a twelve-episode show. That was the number of
 *   files *looked at* across every copy the group publishes, and once the
 *   rows are split by edition the only honest number is this row's: twelve.
 * * it carried on past episode 12 with rows 13, 14 and up - the files the
 *   rules refused, which belong to another season or are batches. Useful once,
 *   confusing every other time.
 * * it had a `#` column and an `Episode` column saying almost the same thing,
 *   and a `Verdict` column reading "Yours" all the way down.
 *
 * So the table is **one row per episode**, in order, and the episode number is
 * the only counter. An episode nothing has offered yet gets a row with no file
 * in it rather than being skipped or filled with something else: for an
 * ongoing show that is the whole point - you can see how far the group has got.
 *
 * What was refused is counted above the table rather than listed. The count is
 * the part worth knowing ("46 belonged to another season"); the names are not.
 */
export function SelectionFilesPanel({
  scan,
  episodeCount,
  offset = 0,
  // Aliased: `numbering` is also the helper this file imports for the
  // episode-range note, and one name cannot be both.
  numbering: numberingGroup,
  onClose,
  batch,
  owned,
}: {
  scan: SelectionScan;
  /** What the metadata source says the season holds, for the empty rows. */
  episodeCount: number | null;
  /**
   * How far this group's numbering runs past episode 1 (build 112).
   *
   * Zero for a group that restarts each season. 66 for one that counts from
   * the start of the franchise, in which case every row says both numbers -
   * "1 (67)" - because both are true and the file on the tracker is called
   * the second one.
   */
  offset?: number;
  /**
   * Where this season starts and ends, rendered by the dialog (build 120).
   *
   * Passed in rather than built here: the two boxes are driven by the
   * dialog's state - the pool lookup, the manual override, the resolve
   * button - and only their *place* was wrong. Asked for: *"as Files is now
   * always visible, move the First episode is / last episode will be also in
   * this files screen, directly below files and the text '3 files, episodes
   * 55-57'"*.
   */
  numbering?: ReactNode;
  /**
   * Given only where this panel is one of several and can be dismissed - the
   * Release Inspector (build 124).
   *
   * The setup dialog passes nothing, and gets no heading and no close button
   * with it: there it is the fixed right-hand half of the dialog, always
   * present, and the × closed nothing at all - the column rendered whenever
   * a scan existed regardless of it. A heading over a permanent column is a
   * line of text saying where you already are.
   */
  onClose?: () => void;
  /**
   * What taking single episodes out of the group's batch would do (build 213).
   *
   * The group only has the season as one torrent, so its files are read from the batch and the missing
   * episode's file is named in the table, as the one that will be downloaded.
   */
  /**
   * Episodes the library already has (build 221). The list is what the group offers, not what will be
   * downloaded, so with these the owned ones are dimmed and say so instead of looking like work to do.
   */
  owned?: ReadonlySet<number>;
  batch?: {
    loading: boolean;
    error: string | null;
    name: string | null;
    rows: BatchRow[];
    leftAlone: number;
  } | null;
}) {
  const mine = scan.releases.filter((release) => release.accepted);
  const batchRows = batch?.rows ?? [];
  const fromBatch = new Map(batchRows.map((row) => [row.episode, row]));
  const singleRows = episodeRows(mine, episodeCount, offset);
  // A group with only a batch has no single-episode rows at all; the ones it can fill still get theirs.
  const rows = singleRows.length > 0
    ? singleRows
    : batchRows.map((row) => ({ number: row.episode, files: [] as typeof singleRows[number]["files"] }));
  const note = numbering(scan.episodes.map((number) => number - offset), episodeCount);
  const duplicates = duplicatesByEpisode(
    mine.map((release) => ({ number: release.number, tags: release.tags })),
  );
  const waiting = rows.filter((row) => row.files.length === 0 && !fromBatch.has(row.number)).length;

  return (
    <div className="selection-files">
      {/* Only where it can actually be closed (build 124). See `onClose`. */}
      {onClose && (
        <div className="dialog-head">
          <h3>{t("scan.filesTitle")}</h3>
          <button className="icon-button" onClick={onClose} aria-label={t("scan.close")}>✕</button>
        </div>
      )}
      {/* This row's own count - and with an offset set, this *season's*
          count rather than the row's whole span (build 113). The row holds
          three seasons of Re:Zero; nineteen of those files are the one being
          added, and saying 44 was true of nothing anybody asked for. */}
      <p className="muted" style={{ margin: 0 }}>
        {t("scan.filesSummary")
          .replace("{files}", String(rows.reduce((total, row) => total + row.files.length, 0)))
          .replace("{episodes}", episodeRanges(rows.map((row) => row.number)) || "-")}
        {waiting > 0 && ` · ${t("scan.waiting").replace("{n}", String(waiting))}`}
      </p>

      {/* What will come out of the batch, said before the table so nobody has to work it out from a row (213). */}
      {batch && (
        <div className="batch-note">
          {batch.loading && <p className="muted">{t("batch.reading")}</p>}
          {batch.error && <p className="error-message">{t("batch.failed").replace("{reason}", batch.error)}</p>}
          {!batch.loading && !batch.error && batch.rows.length > 0 && (
            <>
              <strong>{t("batch.willTake").replace("{list}", episodeRanges(batch.rows.map((row) => row.episode)))}</strong>
              {batch.name && <small className="muted">{batch.name}</small>}
              {batch.leftAlone > 0 && (
                <small className="muted">{t("batch.others").replace("{n}", String(batch.leftAlone))}</small>
              )}
            </>
          )}
          {!batch.loading && !batch.error && batch.rows.length === 0 && <p className="muted">{t("batch.nothing")}</p>}
        </div>
      )}

      {/* Directly below that line, which is what it is about. */}
      {numberingGroup}

      {duplicates.size > 0 && (
        <p className="warning-message">
          {t("scan.duplicates")
            .replace("{n}", String(duplicates.size))
            .replace("{list}", episodeRanges([...duplicates.keys()]))}
        </p>
      )}
      {/* The "this group counts from the start of the series" warning has
          gone (build 113). It was the question; the fields in the header are
          the answer, and a warning that stays after its own answer is just
          noise. It is still shown when nothing has been decided yet. */}
      {note.absolute && offset === 0 && (
        <p className="warning-message" title={t("scan.absoluteHint")}>
          {t("scan.absolute")
            .replace("{first}", String(note.first))
            .replace("{last}", String(note.last))
            .replace("{offset}", String(note.offset))}
        </p>
      )}

      <div className="scan-table-wrap">
        <table className="scan-table">
          <thead>
            <tr>
              {/* "#" rather than "Episode" (build 124): the column holds
                  one or two digits and the word was three times wider than
                  its own contents. */}
              <th className="numeric">{t("scan.colEpisodeShort")}</th>
              <th>{t("scan.colFile")}</th>
              <th className="numeric">{t("scan.colSize")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.number}
                className={
                  owned?.has(row.number) && row.files.length
                    ? "scan-row-owned"
                    : row.files.length ? "" : fromBatch.has(row.number) ? "scan-row-batch" : "scan-row-waiting"
                }
              >
                <td className="episode-number">
                {row.number}
                {/* The group's own number beside this season's, when they
                    differ - the file on the tracker is called that. */}
                {offset > 0 && <small className="muted"> ({row.number + offset})</small>}
              </td>
                <td>
                  {row.files.length === 0 && fromBatch.has(row.number) ? (
                    <>
                      <code className="scan-file">{fromBatch.get(row.number)?.name}</code>
                      <small className="batch-tag">{t("batch.fromBatch")}</small>
                    </>
                  ) : row.files.length === 0 ? (
                    /* Deliberately blank. An episode nobody has published is
                       not a file, and putting anything in this cell - least of
                       all a different episode's name - is what made the old
                       list confusing. */
                    <span className="muted">—</span>
                  ) : (
                    <>
                      {row.files.map((file) => (
                        <span key={file.raw_name} className="scan-file-line">
                          <code className="scan-file">{file.raw_name}</code>
                          <SeederMark seeders={file.seeders} />
                        </span>
                      ))}
                      {owned?.has(row.number) && <small className="owned-tag">{t("scan.owned")}</small>}
                    </>
                  )}
                </td>
                <td className="numeric">
                  {row.files.length === 0 && fromBatch.has(row.number) && (
                    <span className="scan-file">{shortSize(fromBatch.get(row.number)?.size ?? null)}</span>
                  )}
                  {row.files.map((file) => (
                    <span key={file.raw_name} className="scan-file">{shortSize(file.size)}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && <p className="muted">{t("setup.noneFound")}</p>}

      {/* "Also seen, and why it was left out" was here until build 120.
          Removed on request - *"its not needed"*. It listed every term that
          was searched and every rule that rejected something, which was
          useful while the search was being built and is noise now that it
          works. The Release Inspector still shows all of it. */}

      {scan.failures.length > 0 && (
        <p className="error-message">
          {t("setup.indexerUnreachable")}
          <small className="muted" style={{ display: "block", marginTop: 4 }}>
            {scan.failures.join("; ")}
          </small>
        </p>
      )}
    </div>
  );
}

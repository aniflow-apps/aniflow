/**
 * On an empty watchlist: "your libraries already hold anime" (build 254).
 *
 * A fresh install with a library full of anime looked finished and empty. This asks once, quietly, and only says
 * something when there is something to import - the folders no anime has, in a library AniFlow writes to. Pressing
 * the button opens the Import folders window; nothing is added until the person confirms there.
 */
import { useEffect, useState } from "react";

import { listImportCandidates } from "../api/client";
import { t } from "../i18n";
import { ImportFoldersDialog } from "./ImportFoldersDialog";

export function ImportFoldersPrompt({ onImported }: { onImported: () => void }) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    listImportCandidates()
      .then((rows) => {
        if (live) setCount(rows.filter((row) => row.tracked_as === null).length);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (count === 0 && !open) return null;
  return (
    <>
      {count > 0 ? (
        <section className="import-prompt">
          <h3>{t("import.empty.title")}</h3>
          <p className="muted">{t("import.empty.body").replace("{n}", String(count))}</p>
          <button className="button primary" onClick={() => setOpen(true)}>
            {t("import.empty.button")}
          </button>
        </section>
      ) : null}
      {open ? (
        <ImportFoldersDialog
          onClose={() => {
            setOpen(false);
            setCount(0);
          }}
          onDone={onImported}
        />
      ) : null}
    </>
  );
}

/**
 * "No torrent client is set up" (build 227).
 *
 * Found on a clean install: an anime was added, its episode said "Ready to
 * download" with a button, and pressing it did nothing visible - without a
 * client address the release just stays pending, and the only word about it
 * went to the server log. Every screen about downloading says so now, with a
 * way to the setting.
 *
 * Asks for itself rather than being handed the flag: the place it is shown is
 * also the place somebody comes back to after fixing it, and a copy read once
 * at startup would go on warning until the page was reloaded.
 */
import { useEffect, useState } from "react";

import { getAppInfo } from "../api/client";
import { t } from "../i18n";

export function NoClientNotice() {
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let live = true;
    void getAppInfo()
      .then((info) => {
        // Basic mode has no downloading at all, so there is nothing to warn about.
        if (live) setMissing(info.app_mode === "advanced" && !info.torrent_client_configured);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (!missing) return null;
  return (
    <p className="client-notice" role="alert">
      <span aria-hidden="true">&#9888;</span>
      <span>
        <b>{t("client.missingTitle")}</b> {t("client.missingBody")}
      </span>
      <a className="button secondary" href="#/settings/downloads">
        {t("client.open")}
      </a>
    </p>
  );
}

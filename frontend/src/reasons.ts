import { t } from "./i18n";

/**
 * Turning the server's machine words into the user's words (build 78).
 *
 * The backend reports why something stopped as an identifier -
 * ``missing_in_client``, ``subgroup_mismatch`` - because that is what belongs
 * in a database column and in a log. Two screens printed those identifiers
 * straight onto the page: the Activity list showed ``missing_in_client`` and
 * the RSS page showed ``subgroup_mismatch, resolution_mismatch``.
 *
 * ``t()`` falls back to the key it was given, so a missing translation is
 * invisible until somebody reads that screen - which is how both of these
 * lasted several builds. These two helpers make the fallback *the identifier
 * itself* rather than the full key, so the worst case is what was already
 * being shown, and never ``import.reason.some_new_thing``.
 */

/** Whether a string is one of the server's identifiers rather than a sentence. */
function looksLikeCode(value: string): boolean {
  return /^[a-z][a-z0-9_]*$/.test(value);
}

function translate(prefix: string, code: string): string {
  const key = `${prefix}${code}`;
  const text = t(key);
  // t() returns the key when there is no translation for it.
  return text === key ? code : text;
}

/**
 * Why a download is stuck, in words.
 *
 * Free text is passed through: the same field also carries what another
 * program said ("qBittorrent is not configured", an HTTP error), and those are
 * already sentences.
 */
export function reasonText(detail: string): string {
  return looksLikeCode(detail) ? translate("import.reason.", detail) : detail;
}

/**
 * What qBittorrent says a torrent is doing, in words.
 *
 * Its states are camel-case identifiers - "stalledUP", "queuedDL" - and they
 * were printed as-is. Anything unrecognised falls back to the raw state,
 * because a client version reporting something new should still say something.
 */
export function qbStateText(state: string | null | undefined): string {
  if (!state) return "";
  const key = `dl.qbState.${state}`;
  const text = t(key);
  return text === key ? state : text;
}

/**
 * Why a release did not match a profile, in words.
 *
 * The codes are built by the matching engine as ``<field>_mismatch`` or
 * ``<field>_unknown`` (services/matching.py), and the difference matters: a
 * mismatch means the release says something else, unknown means the release
 * does not say at all.
 */
export function matchFailureText(code: string): string {
  return looksLikeCode(code) ? translate("match.fail.", code) : code;
}

/**
 * Why a rule refused a release, in words.
 *
 * The same `match.fail.*` vocabulary the RSS page reads, so the profile test
 * and the More info window cannot drift into a second set of phrasings for
 * the same codes.
 */
export function matchFailText(code: string): string {
  return looksLikeCode(code) ? translate("match.fail.", code) : code;
}

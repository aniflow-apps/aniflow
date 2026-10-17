import type { AnimeSearchResult, AnimeSummary } from "./api/client";

/**
 * "Is this already in my watchlist?", answered in the browser (build 78).
 *
 * The server answers the same question and matches on ids the browser never
 * sees, so its flag is still trusted when it says yes. But that flag arrives
 * with a season line-up cached for hours, while the watchlist changes as you
 * use the app - delete an anime and it stayed hidden behind *Hide anime
 * already in your library* until the cache expired.
 *
 * So the watchlist in memory gets a say. It is the one thing that is always
 * current, it costs a comparison per card, and it makes deleting take effect
 * on the next render.
 */

/**
 * A title reduced to what two sources can be expected to agree on.
 *
 * The smallest useful form of the backend's own comparison
 * (services/release_parser.normalize_title_for_match): case, punctuation and
 * spacing. Deliberately not clever - a false match here hides a card behind a
 * filter the user can switch off, and being offered a second copy of
 * something already downloading is the worse mistake.
 */
export function matchable(title: string): string {
  const folded = foldLetters(title);
  // A title with letters of another script keeps them (the backend's `title_key`): reduced to
  // a-z0-9 a Japanese "佐々木とピーちゃん 第２期" is just "2", and so is every other show's.
  if (Array.from(folded).some((char) => char.charCodeAt(0) > 127 && /\p{L}/u.test(char))) {
    return title.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  }
  return longVowels(folded.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim());
}

/* Letters a title is styled with that a reader takes for another (build 209), the same table as the
   backend's release_parser: "PSYЯEN" is PSYREN, "$" is S, "€" is E, "IDOLM@STER" is IDOLMASTER. */
const LOOKALIKES: Record<string, string> = {
  "$": "s", "€": "e", "£": "l", "¥": "y", "@": "a",
  "Я": "r", "я": "r", "И": "n", "и": "n", "∀": "a", "Ʌ": "a",
};

/** Look-alikes and accents folded to plain letters: "Ōkami" and "Okami" meet. */
export function foldLetters(text: string): string {
  const mapped = Array.from(text, (char) => LOOKALIKES[char] ?? char).join("");
  return mapped.normalize("NFKD").replace(/[̀-ͯ]/g, "");
}

/** "Kuu" and "Ku", "Kyouka" and "Kyoka". Not "ii": a roman numeral II would become I. */
export function longVowels(text: string): string {
  return text.replace(/oo|ou|uu/g, (found) => found[0]);
}

/** Whether the watchlist already has the show this candidate describes. */
export function tracksAlready(library: AnimeSummary[], candidate: AnimeSearchResult): boolean {
  if (candidate.in_library) return true;
  const wanted = [
    candidate.display_name,
    candidate.title_english,
    candidate.title_romaji,
    candidate.title_native,
  ]
    .filter((name): name is string => Boolean(name))
    .map(matchable)
    // A title with no Latin letters - every native one - reduces to "", and
    // "" equals "" (build 155). Nothing is known about a show by its empty
    // name, so neither side may match on one.
    .filter(Boolean);
  const known = (name: string | null | undefined) => {
    const key = matchable(name ?? "");
    return key !== "" && wanted.includes(key);
  };
  return library.some((item) =>
    (item.primary_provider === candidate.provider && item.primary_provider_id === candidate.provider_id)
    || known(item.display_name)
    || known(item.local_name),
  );
}

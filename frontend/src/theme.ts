/**
 * Wearing the selected theme (build 90).
 *
 * The interface is built out of about thirty CSS custom properties, and a
 * theme is a set of values for them - see backend/app/services/themes.py. So
 * applying one is a stylesheet with a single ``:root`` rule in it, loaded
 * after the bundle: same specificity, later wins.
 *
 * A ``<link>`` rather than fetching the text and putting it in a ``<style>``,
 * because a link element gets the browser's own error handling: if the
 * request fails the page keeps the bundled palette and nothing has to catch
 * anything. Which is also why the load is not awaited anywhere - a theme
 * that never arrives is a page that looks like the default, not a page that
 * does not appear.
 *
 * ``active.css`` is deliberately used rather than the slug: it is the one
 * theme endpoint reachable without a session, so the login screen is themed
 * too. Somebody running the bright theme meeting a dark login page would
 * reasonably conclude something was broken.
 */

const ELEMENT_ID = "aniflow-theme";

/**
 * Point the page at the active theme, or at a specific one.
 *
 * Called once at startup and again whenever the setting is saved. The URL
 * carries a cache-buster so re-selecting the same theme after editing its
 * file actually re-reads it - which is the thing somebody writing a theme
 * does over and over.
 */
export function applyTheme(slug?: string): void {
  const file = slug ? `${encodeURIComponent(slug)}.css` : "active.css";
  const href = `/api/themes/${file}?v=${Date.now()}`;
  let link = document.getElementById(ELEMENT_ID) as HTMLLinkElement | null;
  if (link === null) {
    link = document.createElement("link");
    link.id = ELEMENT_ID;
    link.rel = "stylesheet";
    document.head.append(link);
  }
  link.href = href;
}

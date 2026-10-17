/**
 * The two small buttons beside the watchlist's name filter (build 263): the adult filter and "My taste".
 *
 * They were text buttons at the bottom of a long page, where nobody looks for a filter. Next to the search box they
 * are where the other filtering is, and as icons they take no room. Drawn inline like the sidebar's icons: no
 * library, and `currentColor` carries the state.
 */

/** "18+", struck through while adult anime are hidden - the filter is on. */
export function AdultIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg className="watchlist-icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
         strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.5" y="5.5" width="19" height="13" rx="3" />
      <text x="12" y="15.3" textAnchor="middle" fontSize="8.4" fontWeight="700" fill="currentColor" stroke="none"
            fontFamily="inherit">18+</text>
      {hidden ? <path d="M4 20 20 4" /> : null}
    </svg>
  );
}

/** A heart: what you like, which is what the taste window shows. */
export function TasteIcon() {
  return (
    <svg className="watchlist-icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
         strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 20.5s-7.5-4.6-9.2-9.3C1.7 8 3.6 5 6.7 5c1.9 0 3.5 1 5.3 3 1.8-2 3.4-3 5.3-3 3.1 0 5 3 3.9 6.2-1.7 4.7-9.2 9.3-9.2 9.3z" />
    </svg>
  );
}

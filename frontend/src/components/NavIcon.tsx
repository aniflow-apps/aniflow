/**
 * Sidebar icons, drawn inline.
 *
 * No icon library: seven line drawings are not worth a dependency, and inline
 * SVG inherits `currentColor` so the active state needs no second asset.
 */
const PATHS: Record<string, string[]> = {
  // A list with markers - the watchlist.
  dashboard: ["M8 6h13", "M8 12h13", "M8 18h13", "M3.5 6h.01", "M3.5 12h.01", "M3.5 18h.01"],
  calendar: ["M3 5.5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M8 2.5v4", "M16 2.5v4", "M3 10h18"],
  // A folder - a library is somewhere files live (build 192).
  libraries: ["M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  // Sliders - a profile is a set of rules.
  profiles: ["M5 21v-7", "M5 10V3", "M12 21v-9", "M12 8V3", "M19 21v-5", "M19 12V3", "M2.5 14h5", "M9.5 8h5", "M16.5 16h5"],
  // Broadcast waves - a feed.
  // A magnifier: an indexer is the thing that gets asked questions.
  indexers: ["M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13", "M15.5 15.5 21 21"],
  rssSources: ["M4 11a9 9 0 0 1 9 9", "M4 4a16 16 0 0 1 16 16", "M5.5 19.5h.01"],
  downloads: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
  // Same mark as Downloads (build 164): this page is what "where is my
  // download" grew into, and it wears the icon that already meant that.
  progress: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
  // A ticked box - the checklist (build 232).
  checks: ["M9 11l3 3L22 4", "M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"],
  // A pulse line - things that happened.
  activity: ["M22 12h-4l-3 8-4-16-3 8H2"],
  attention: ["M12 9v4", "M12 17h.01", "M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z"],
  // A magnifier over a list - looking closely at what came back. The debug
  // page (build 80), which is expected to be temporary.
  inspector: [
    "M4 5h9",
    "M4 9.5h6",
    "M4 14h5",
    "M14.5 12.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
    "M17.6 19.6 21 23",
  ],
  // Three columns of different heights - the same season answered by
  // several sources, which is what the comparison page shows. Build 83,
  // expected to be temporary.
  sources: [
    "M4 20V9",
    "M10 20V4",
    "M16 20v-8",
    "M22 20v-5",
    "M2 20h20",
  ],
  // Stacked discs - somebody else's library, which is what Shoko keeps.
  shoko: [
    "M12 3.5c4.4 0 8 1.1 8 2.5s-3.6 2.5-8 2.5-8-1.1-8-2.5 3.6-2.5 8-2.5",
    "M4 6v12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V6",
    "M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5",
  ],
  // A bookmarked page - the AniDB My List (build 258).
  anidb: ["M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"],
  settings: [
    "M12 2.5v3", "M12 18.5v3", "M2.5 12h3", "M18.5 12h3",
    "M5.2 5.2l2.1 2.1", "M16.7 16.7l2.1 2.1", "M18.8 5.2l-2.1 2.1", "M7.3 16.7l-2.1 2.1",
  ],
  // A clock face - what and when things run on their own.
  schedule: ["M12 7v5.3l3.3 2"],
};

const CIRCLES: Record<string, [number, number, number]> = {
  settings: [12, 12, 3.2],
  schedule: [12, 12, 8.5],
};

export function NavIcon({ name }: { name: string }) {
  const paths = PATHS[name];
  if (!paths) return null;
  const circle = CIRCLES[name];
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths.map((d) => <path key={d} d={d} />)}
      {circle && <circle cx={circle[0]} cy={circle[1]} r={circle[2]} />}
    </svg>
  );
}

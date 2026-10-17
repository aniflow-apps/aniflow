/**
 * What one episode row says about itself (build 93).
 *
 * The row's own arithmetic, decided apart from the markup that draws it. Every
 * defect it has had was in here rather than in the pixels:
 *
 * * "Complete, in library" printed beside 0% and "Seeding", for a file that
 *   had been moved away and a torrent that had been removed on purpose
 *   (build 88);
 * * the media-server verdict welded onto "In library" with a dash, so a real
 *   release name wrapped onto three lines (build 90) - and then given a column
 *   of its own, which centred it against the *filename* and truncated that
 *   instead (build 92);
 * * the Download button offered for an episode with no release to hand over.
 *
 * Which statuses can be downloaded, and which mean the torrent client owns the
 * row, are passed in rather than imported: they belong to the page, and this
 * file should not need changing when one of them gains a member.
 */

/** Just the part of an episode this needs. */
export interface EpisodeLike {
  number: number;
  status: string;
  releases: readonly { raw_name?: string | null; magnet_url?: string | null }[];
  media: {
    filename?: string | null;
    summary?: string | null;
  };
  view?: { state: string; detail: string | null };
  download?: {
    status?: string | null;
    filename?: string | null;
    progress?: number | null;
    info_hash?: string | null;
    attention_reason?: string | null;
  } | null;
  error?: string | null;
}

export interface RowView {
  /** Whether the episode's file is in the library. */
  owned: boolean;
  /** Whether the Download button should be offered at all. */
  canDownload: boolean;
  /** The name to show, for whichever stage the episode is in. */
  filename: string | null;
  /** True when that name is the file in the library rather than the
   *  release it came from - which tag it carries (build 153). */
  filenameIsTarget: boolean;
  /** The release it came from, when that is a different string. */
  secondName: string | null;
  /**
   * The one state a person reads, from the server (build 125).
   *
   * This replaced `statusFrom`, which said *which of two vocabularies* to
   * word the row in - the episode's nine statuses or the download's seven.
   * Choosing between them per row is what let one episode read "Owned" on one
   * line and "In library" on the next. Now there is one answer, derived in
   * one place: services/episode_view.py.
   */
  state: string;
  /** Where or why, when there is more to say. */
  stateDetail: string | null;
  /** What to put in the title attribute of the magnet marker. */
  magnetTitle: string | null;
}

export function episodeRowView(
  episode: EpisodeLike,
  rules: { downloadable: ReadonlySet<string>; torrentOwns: ReadonlySet<string> },
): RowView {
  const owned = episode.status === "COMPLETE";
  const best = episode.releases[0] ?? null;

  /* A Download button needs something to hand over. Offering one with no
     release behind it is a button that reports failure when pressed. */
  const canDownload = rules.downloadable.has(episode.status) && episode.releases.length > 0;

  const filename = episode.media.filename ?? episode.download?.filename ?? best?.raw_name ?? null;
  /* Both names when they differ: the one on disk, and the release it came
     from. In Shoko Server mode they always differ - Shoko renames the file -
     and the release name is what all matching uses, so seeing both is what
     makes either checkable. */
  const releaseName = episode.download?.filename ?? best?.raw_name ?? null;
  const secondName = filename && releaseName && releaseName !== filename ? releaseName : null;

  /* The server's answer, or a safe fallback. The fallback exists for one
     reason: a payload cached by the browser from before this build has no
     `view`, and a row that renders its own key at the user is worse than a
     row that is briefly vague. */
  const state = episode.view?.state ?? "";
  const stateDetail = episode.view?.detail ?? null;

  return {
    owned,
    canDownload,
    filename,
    filenameIsTarget: Boolean(episode.media.filename),
    secondName,
    state,
    stateDetail,
    magnetTitle: best?.magnet_url
      ? `magnet: ${best.magnet_url}`
      : episode.download?.info_hash
        ? `hash: ${episode.download.info_hash}`
        : null,
  };
}

/**
 * How big an episode's number is drawn (build 189).
 *
 * The box the number sits in was made narrow to give the title room, so the
 * text has to shrink for the numbers that no longer fit it: three digits a
 * step smaller, four a step smaller again. Space for 9999 episodes, which is
 * more than any series has but fewer than a counter somebody mistyped.
 */
export function episodeNumberSize(number: number): "n2" | "n3" | "n4" {
  const digits = String(Math.abs(Math.trunc(number))).length;
  if (digits >= 4) return "n4";
  if (digits === 3) return "n3";
  return "n2";
}

/**
 * Whether this episode has two files in the library (build 189): the one it
 * uses, and an original kept by a release change or an older version. The row
 * is framed in red until somebody chooses which to delete. A kept original
 * with no file of the episode's own yet is the plan, not a problem - the new
 * release has not arrived.
 */
export function hasTwoFiles(episode: {
  media: { filename?: string | null };
  copies?: readonly { kind: string }[];
}): boolean {
  if (!episode.media.filename) return false;
  return (episode.copies ?? []).some((copy) => copy.kind === "kept_original" || copy.kind === "previous_version");
}

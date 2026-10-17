/**
 * The files of one episode, as the file window lists them (build 189).
 *
 * An episode normally has a Target (the file in the library) and a Source (the
 * release it came from). A release change, a v2 or a kept original adds more -
 * written down by the server as the episode's `copies` - and each is given a
 * tag that says what it is, so the list reads like the example it was asked
 * for: Target, Source, Backup, an older version.
 *
 * Pure, so the tags can be tested without drawing anything.
 */
import type { EpisodeCopy } from "./api/client";

export type FileTag = "target" | "source" | "backup" | "original" | "previous" | "waiting";

export interface FileRow {
  key: string;
  tag: FileTag;
  /** Extra tags that compare a copy with the episode's own file. */
  extras: ("lower" | "alternative")[];
  filename: string;
  /** The note to delete when this row's button is pressed; null = no button. */
  copyId: string | null;
}

interface EpisodeForFiles {
  media: { filename?: string | null };
  download?: { filename?: string | null } | null;
  releases: readonly { raw_name?: string | null }[];
  copies?: readonly EpisodeCopy[];
}

/** "1080p" out of a release name, as a number, or null. */
export function resolutionOf(name: string | null | undefined): number | null {
  const found = /(?:^|[^0-9])(\d{3,4})p(?![a-z0-9])/i.exec(name ?? "");
  return found ? Number(found[1]) : null;
}

/** "SubsPlease" out of "[SubsPlease] Title - 01 (720p).mkv", or null. */
export function groupOf(name: string | null | undefined): string | null {
  const found = /^\s*\[([^\]]+)\]/.exec(name ?? "");
  return found ? found[1].trim().toLowerCase() : null;
}

const TAG_FOR_KIND: Record<string, FileTag> = {
  renamed: "backup",
  kept_original: "original",
  previous_version: "previous",
  awaiting_replacement: "waiting",
};

/** What this episode's file window shows, top to bottom. */
export function fileRows(episode: EpisodeForFiles): FileRow[] {
  const rows: FileRow[] = [];
  const target = episode.media.filename ?? null;
  const source = episode.download?.filename ?? episode.releases[0]?.raw_name ?? null;

  if (target) rows.push({ key: "target", tag: "target", extras: [], filename: target, copyId: null });
  if (source && source !== target) {
    rows.push({ key: "source", tag: "source", extras: [], filename: source, copyId: null });
  }

  const mine = resolutionOf(target);
  const myGroup = groupOf(target);
  for (const copy of episode.copies ?? []) {
    const tag = TAG_FOR_KIND[copy.kind];
    if (!tag) continue;
    const extras: FileRow["extras"] = [];
    // Only a real file next to the episode's own is worth comparing with it.
    if (tag !== "backup" && tag !== "waiting") {
      const theirs = copy.resolution ? Number(String(copy.resolution).replace(/p$/i, "")) : resolutionOf(copy.filename);
      if (mine !== null && theirs !== null && !Number.isNaN(theirs) && theirs < mine) extras.push("lower");
      const theirGroup = groupOf(copy.filename);
      if (myGroup && theirGroup && myGroup !== theirGroup) extras.push("alternative");
    }
    rows.push({
      key: copy.id,
      tag,
      extras,
      filename: copy.filename,
      // Not the one waiting for its replacement: until that arrives it is the
      // only file the episode has.
      copyId: tag === "waiting" ? null : copy.id,
    });
  }
  return rows;
}

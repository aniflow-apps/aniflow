/**
 * What the feeds hold for one anime (build 153).
 *
 * The Feed check on the detail page ran the library-wide analyzer and showed
 * all of it. Reported against 152: *"why does it list so much? All of them do
 * not even have the correct name or quality"* - and a Skeleton Knight release
 * at 100% on Trapped in a Dating Sim's page. The 100% was right: it is how
 * sure the analyzer was that the release is *Skeleton Knight*. Nothing on the
 * page said so, and "Use this release" there would have taught this anime
 * another show's name.
 *
 * So the list is narrowed to this anime: its own confidence for each release
 * (not the release's best match's), at least `MIN_CONFIDENCE`, and never a
 * release that matches another tracked anime better.
 */
import type { AnalyzeItem } from "./api/client";

/** Below this, a release is not about this anime - "nobody needs the 0%". */
export const MIN_CONFIDENCE = 80;

export interface ProfileLike {
  subgroups: string[];
  resolution: string | null;
}

/** How sure the analyzer is that `item` is this anime, 0-100. */
export function confidenceFor(item: AnalyzeItem, animeId: string): number {
  if (item.best_anime_id === animeId) return item.confidence;
  return item.candidates.find((candidate) => candidate.anime_id === animeId)?.confidence ?? 0;
}

/** The releases worth showing on this anime's page, best first. */
export function releasesFor(items: readonly AnalyzeItem[], animeId: string): AnalyzeItem[] {
  const mine: AnalyzeItem[] = [];
  for (const item of items) {
    const confidence = confidenceFor(item, animeId);
    if (confidence < MIN_CONFIDENCE) continue;
    // Another show's release, even if it resembles this one.
    if (item.best_anime_id && item.best_anime_id !== animeId && item.confidence >= confidence) continue;
    const own = item.candidates.find((candidate) => candidate.anime_id === animeId);
    mine.push({
      ...item,
      confidence,
      classification: item.best_anime_id === animeId
        ? item.classification
        : ((own?.classification as AnalyzeItem["classification"] | undefined) ?? item.classification),
      best_anime_id: animeId,
      // The row would only repeat this anime's own name.
      best_name: null,
    });
  }
  return mine.sort((a, b) => b.confidence - a.confidence);
}

/** Why the release profile would pass this one over, or null if it fits. */
export function profileMisfit(item: AnalyzeItem, profile: ProfileLike | null | undefined): "resolution" | "subgroup" | null {
  if (!profile) return null;
  if (profile.resolution && item.resolution && item.resolution !== profile.resolution) return "resolution";
  const groups = profile.subgroups.map((group) => group.toLowerCase());
  if (groups.length && item.subgroup && !groups.includes(item.subgroup.toLowerCase())) return "subgroup";
  return null;
}

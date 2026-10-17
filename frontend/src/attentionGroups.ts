/**
 * Which attention records belong to the same problem (build 125).
 *
 * Asked for like this: *"qBittorrent goes offline and Episodes 1-4 fail at
 * roughly the same time. Instead of 'Episode 1 failed / Episode 2 failed /
 * Episode 3 failed / Episode 4 failed' prefer '4 episodes could not be
 * acquired'."*  And, in the same breath: *"Do not invent an unnecessarily
 * complex grouping system if the existing information cannot reliably support
 * it."*
 *
 * It can, and the answer is smaller than it looks. The failures that belong
 * together **already share a reason code**: a qBittorrent outage gives every
 * affected row the same `detail`, because one place decides it. So grouping is
 * a key, not a clustering algorithm:
 *
 *     (kind, anime, detail)  ->  one group
 *
 * The anime joined the key in build 128, after two different shows were
 * collapsed into one row: *"the grouping should not be based on the tag, it
 * should be based on the tag + the same anime."*
 *
 * Deliberately **no time window.** Proximity would be the obvious signal and
 * it is the wrong one: the queue and sync passes run every 30 seconds and
 * reconcile every 30 minutes, so one outage produces failures spread over
 * minutes or hours rather than a tight cluster. Any window wide enough to
 * catch a real incident is wide enough to merge two.
 *
 * The other half of the request - *"If Episode 1 fails yesterday and Episode 2
 * fails today under different circumstances, they do not necessarily belong
 * together"* - falls out of this for free, because different circumstances
 * mean a different reason code, which means a different key.
 *
 * Two failures with the *same* reason a day apart do still group. That is
 * intentional: they have the same cause and the same fix, so they are one
 * thing to deal with. Telling those apart would need an incident identity
 * that closes when the condition clears, which is not built - and the point of
 * shipping this first is to find out whether it is needed.
 */

/** Just the part of an attention record grouping needs. */
export interface GroupableAttention {
  id: string;
  kind: string;
  detail: string | null;
  anime_id: string | null;
  anime_name: string | null;
  feed_source_id: string | null;
  created_at: string;
}

export interface AttentionGroup<T extends GroupableAttention> {
  /** Stable key, so an open/closed group survives a refresh. */
  key: string;
  kind: string;
  /** The shared reason, when there is one. */
  detail: string | null;
  /** Oldest first, the order they happened in. */
  items: T[];
}

/**
 * The key a record groups under.
 *
 * **The anime is part of it** (build 128). It was not, and that was wrong:
 * *"It grouped 2 different animes, I think the grouping should not be based on
 * the tag, it should be based on the tag + the same anime. ... I could
 * understand it if its same tag and same anime, lets say it was Iron Wok Jan
 * Episode 1 and Episode 3, then it makes sense to group them."*
 *
 * Exactly right, and it matters for more than tidiness: two episodes of one
 * show failing for one reason is one thing to deal with, while two different
 * shows failing is two - they may well need different answers, and collapsing
 * them hides that one of them is fine now and the other is not.
 *
 * A **feed** failure is the exception and keeps grouping by source, because
 * that is genuinely one problem however many anime it affects - which is also
 * how the server raises it.
 */
function keyOf(item: GroupableAttention): string {
  // One problem per source, whatever it affects downstream.
  if (item.feed_source_id) return `${item.kind}::feed::${item.feed_source_id}`;
  // Nothing to scope it to: an installation-wide record.
  if (!item.anime_id) return `${item.kind}::global::${item.detail ?? item.id}`;
  /* Same kind, same anime, same reason. The reason is part of it so that
     "already in your library" and "the folder is missing" stay apart even on
     one show - they are different problems with different fixes. */
  return `${item.kind}::${item.anime_id}::${item.detail ?? "none"}`;
}

/**
 * Group records, keeping the order they arrived in.
 *
 * Groups come back in the order their first member appeared, and members are
 * sorted oldest first - so a list does not reshuffle itself when a fifth
 * episode joins a group of four.
 */
export function groupAttentions<T extends GroupableAttention>(items: T[]): AttentionGroup<T>[] {
  const groups = new Map<string, AttentionGroup<T>>();
  for (const item of items) {
    const key = keyOf(item);
    const existing = groups.get(key);
    if (existing) existing.items.push(item);
    else groups.set(key, { key, kind: item.kind, detail: item.detail, items: [item] });
  }
  for (const group of groups.values()) {
    group.items.sort((a, b) => a.created_at.localeCompare(b.created_at));
  }
  return [...groups.values()];
}

/**
 * How many distinct anime a group covers.
 *
 * Kept as the guard rather than as a headline (build 128). Since the anime is
 * part of the key, an anime-scoped group can only ever cover one - so this
 * returning more than 1 means the keying has regressed, and the test says so.
 */
export function animeCount(group: AttentionGroup<GroupableAttention>): number {
  const seen = new Set(group.items.map((item) => item.anime_id).filter(Boolean));
  return seen.size;
}

/** Whether this group is worth collapsing rather than listing. */
export function isCollapsible(group: AttentionGroup<GroupableAttention>): boolean {
  return group.items.length > 1;
}

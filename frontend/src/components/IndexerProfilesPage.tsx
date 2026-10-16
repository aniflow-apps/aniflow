import { useCallback, useEffect, useState } from "react";
import {
  deleteIndexer,
  listIndexers,
  testIndexer,
  updateIndexer,
  type Indexer,
  type IndexerTestResult,
} from "../api/client";
import { t } from "../i18n";

/**
 * Indexer profiles: the sites AniFlow may search.
 *
 * AniFlow ships none and knows nothing about any particular site. A name and a
 * URL is the whole configuration, and the URL may carry the user's own filters
 * - which is why there are no category or filter dropdowns here. Every site
 * documents its own query parameters; re-modelling them in this UI would mean
 * building a definition for each site AniFlow is not supposed to know about.
 */
export function IndexerProfilesPage({
  configDir = "/config",
  editId,
  onEditHandled,
}: {
  /** Where the configuration folders are, so the page can name the exact
   *  path to put files in rather than describing it. */
  configDir?: string;
  /** Scrolled to this indexer, from the RSS page's "Indexer" button. Comes
   *  from the route, so the link is shareable. */
  editId?: string | null;
  onEditHandled?: () => void;
} = {}) {
  const [indexers, setIndexers] = useState<Indexer[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, IndexerTestResult>>({});

  const load = useCallback(async () => {
    try {
      setIndexers(await listIndexers());
      setError(null);
    } catch {
      setError(t("activity.error"));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Arriving from the RSS page: highlight the indexer that owns the managed
  // feed the user clicked. There is no editor to open any more - its file is
  // what to change - so the card is brought into view instead.
  useEffect(() => {
    if (!editId || !loaded) return;
    document.getElementById(`indexer-${editId}`)?.scrollIntoView({ block: "center" });
    onEditHandled?.();
  }, [editId, loaded, indexers, onEditHandled]);

  async function runTest(indexer: Indexer) {
    setTesting(indexer.id);
    try {
      const result = await testIndexer(indexer.id);
      setResults((current) => ({ ...current, [indexer.id]: result }));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTesting(null);
    }
  }

  /** Adopt the feed form of a URL that the test found working. */
  async function useSuggested(indexer: Indexer, suggested: string) {
    try {
      await updateIndexer(indexer.id, { url: suggested });
      setResults((current) => {
        const next = { ...current };
        delete next[indexer.id];
        return next;
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function makeDefault(indexer: Indexer) {
    try {
      await updateIndexer(indexer.id, { is_default: true });
      await load();
    } catch {
      setError(t("activity.error"));
    }
  }

  async function toggleEnabled(indexer: Indexer) {
    try {
      await updateIndexer(indexer.id, { enabled: !indexer.enabled });
      await load();
    } catch {
      setError(t("activity.error"));
    }
  }

  async function remove(indexer: Indexer) {
    if (!window.confirm(t("indexer.deleteConfirm").replace("{name}", indexer.name))) return;
    try {
      await deleteIndexer(indexer.id);
      await load();
    } catch {
      setError(t("activity.error"));
    }
  }

  return (
    <section className="profiles-page">
      {/* Not a control: the folder is where indexers come from, and saying so
          is the whole instruction. */}
      <p className="warning-message indexer-warning">{t("indexer.shipsNone")}</p>
      <div className="detail-panel" style={{ marginBottom: 16 }}>
        <p className="muted" style={{ margin: 0, maxWidth: "78ch" }}>{t("indexer.howToAdd")}</p>
        <code className="indexer-path">{`${configDir.replace(/\/$/, "")}/indexers`}</code>
      </div>
      {error && <p className="error-message">{error}</p>}

      {!loaded ? null : indexers.length === 0 ? (
        <section className="empty-state">
          <div className="empty-icon">⌕</div>
          <h2>{t("indexer.emptyTitle")}</h2>
          <p>{t("indexer.emptyBody")}</p>
        </section>
      ) : (
        <div className="template-grid">
          {indexers.map((indexer) => {
            const result = results[indexer.id];
            return (
              <article className="template-card" key={indexer.id} id={`indexer-${indexer.id}`}>
                <strong>
                  {indexer.name}
                  {indexer.is_default && <span className="default-badge">{t("indexer.isDefault")}</span>}
                </strong>
                <small className="indexer-url">{indexer.url}</small>
                <small className="muted">
                  {[
                    indexer.enabled ? t("rss.sources.enabled") : t("rss.sources.disabled"),
                    indexer.supports_paging ? t("indexer.paged") : t("indexer.singlePage"),
                    /* Which mechanics are in force is the difference between a
                       real search and the site's latest few items, so it is
                       stated rather than left to be inferred. */
                    indexer.definition
                      ? t("indexer.usingDefinition").replace("{name}", indexer.definition)
                      : t("indexer.usingUrl"),
                    ...(indexer.use_as_rss ? [t("indexer.alsoRssActive")] : []),
                  ].join(" · ")}
                </small>
                {/* What a search actually requests. The two URL forms behave
                    differently enough that showing the result beats explaining
                    the rule. */}
                <small className="muted indexer-url">{t("indexer.example")}: {indexer.example_request}</small>
                {indexer.last_error && !result && (
                  <small className="reasons">{indexer.last_error}</small>
                )}
                {result && (
                  result.ok ? <>
                    <small className="imported-tag">
                      {t("indexer.testOk").replace("{n}", String(result.items))}
                    </small>
                    {result.suggested_url && (
                      <div className="indexer-suggestion">
                        <small className="muted">{t("indexer.suggested")}</small>
                        <small className="indexer-url">{result.suggested_url}</small>
                        <button
                          className="button secondary"
                          onClick={() => void useSuggested(indexer, result.suggested_url as string)}
                        >
                          {t("indexer.useSuggested")}
                        </button>
                      </div>
                    )}
                  </> : <small className="reasons">{result.detail}</small>
                )}
                <div className="action-stack horizontal">
                  <button className="button secondary" disabled={testing === indexer.id} onClick={() => void runTest(indexer)}>
                    {testing === indexer.id ? t("settings.checking") : t("indexer.test")}
                  </button>
                  {!indexer.is_default && indexer.enabled && (
                    <button className="button secondary" onClick={() => void makeDefault(indexer)}>{t("indexer.setDefault")}</button>
                  )}
                  <button className="button secondary" onClick={() => void toggleEnabled(indexer)}>
                    {indexer.enabled ? t("indexer.disable") : t("indexer.enable")}
                  </button>
                  <button className="button danger" onClick={() => void remove(indexer)}>{t("rss.sources.delete")}</button>
                </div>
              </article>
            );
          })}
        </div>
      )}

    </section>
  );
}

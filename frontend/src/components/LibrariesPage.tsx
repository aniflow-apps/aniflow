import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import {
  browseFolders,
  createLibrary,
  deleteLibrary,
  listAnime,
  listLibraries,
  listShokoImportFolders,
  makeLibraryDefault,
  refreshLibraries,
  shiftLibrary,
  updateLibrary,
  type AnimeSummary,
  type FolderBrowsing,
  type Library,
  type LibraryRefresh,
  type LibraryDraft,
  type ShokoImportFolders,
} from "../api/client";
import { formatBytes, LOW_SPACE_BYTES } from "../formatBytes";
import { t } from "../i18n";
import { folderRelations, pathChain, refreshHasNews, shokoWarning, type LibraryPreset } from "../libraries";
import { viewToHash } from "../routing";
import { focusLibraryInWatchlist } from "../watchlistLibraries";
import { useToast } from "../toasts";
import { LibraryMoveDialog, LibraryRemoveDialog } from "./LibraryMoveDialog";
import { ImportFoldersDialog } from "./ImportFoldersDialog";
import { ShokoFoldersDialog } from "./ShokoFoldersDialog";
import { SubfoldersDialog } from "./SubfoldersDialog";
import { useLockedPageScroll } from "../useLockedPageScroll";

/**
 * Libraries (build 192, 0.8.5): named target folders. See docs/libraries-plan.md.
 *
 * This build only makes and edits them - nothing is moved or placed by a
 * library yet. The built-in ones (Airing now, Earlier seasons, Needs
 * attention) can be pointed at a folder but not renamed or removed.
 */
/* The last list the server gave, so coming back to the page shows it at once while the (slow, on a network
   share) disk counts are read again, instead of an empty page for ten seconds. */
let lastLibraries: Library[] = [];

export function LibrariesPage({
  shokoMode,
  onLoaded,
  embedded = false,
}: {
  shokoMode: boolean;
  /** The install wizard's copy (build 253): without the special library, the watchlist link and Move anime. */
  embedded?: boolean;
  /** Called with the libraries every time they are read - the install wizard waits for a usable one (build 250). */
  onLoaded?: (libraries: Library[]) => void;
}) {
  const { show } = useToast();
  const [libraries, setLibraries] = useState<Library[]>(lastLibraries);
  const [shoko, setShoko] = useState<ShokoImportFolders | null>(null);
  const [editing, setEditing] = useState<Library | "new" | null>(null);
  /* Match Shoko folders: the window, and the library being made from one of its rows. */
  const [matching, setMatching] = useState(false);
  const [preset, setPreset] = useState<LibraryPreset | null>(null);
  /* Add from folder: choosing the parent, then the list of its subfolders. */
  const [choosingParent, setChoosingParent] = useState(false);
  /* Import folders: the window that turns the folders no anime has into watchlist entries (build 254). */
  const [importing, setImporting] = useState(false);
  const [parent, setParent] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [anime, setAnime] = useState<AnimeSummary[]>([]);
  const [movingFrom, setMovingFrom] = useState<Library | null>(null);
  const [removing, setRemoving] = useState<Library | null>(null);
  /* Which refresh is running (a library id, or all), and what the last one found. */
  const [refreshing, setRefreshing] = useState<number | "all" | null>(null);
  const [refreshed, setRefreshed] = useState<LibraryRefresh[] | null>(null);

  const reload = useCallback(async () => {
    try {
      // The libraries first, without the folder walk, so the page is there at once; then the counts.
      const quick = await listLibraries(true);
      // Until the walk is back, keep the counts already shown rather than blanking them to 0.
      const known = new Map(lastLibraries.map((l) => [l.id, l]));
      const provisional = quick.libraries.map((l) => {
        const before = known.get(l.id);
        return before ? { ...l, anime_with_folder: before.anime_with_folder, folders_on_disk: before.folders_on_disk } : l;
      });
      lastLibraries = provisional;
      setLibraries(provisional);
      onLoaded?.(provisional);
      void listAnime().then(setAnime).catch(() => undefined);
      const answer = await listLibraries();
      lastLibraries = answer.libraries;
      setLibraries(answer.libraries);
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    }
  }, [show, onLoaded]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Shoko is asked only in Shoko mode: standalone has no folders to report.
  useEffect(() => {
    if (!shokoMode) return;
    void listShokoImportFolders().then(setShoko).catch(() => setShoko(null));
  }, [shokoMode]);

  async function remove(id: number) {
    try {
      await deleteLibrary(id);
      setConfirming(null);
      await reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    }
  }

  async function refresh(target: number | "all") {
    setRefreshing(target);
    try {
      setRefreshed(await refreshLibraries(target === "all" ? undefined : target));
      await reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    } finally {
      setRefreshing(null);
    }
  }

  /* Upcoming never goes, and there must always be one library with a folder. */
  const foldered = libraries.filter((l) => l.has_folder).length;
  const cannotRemove = (library: Library) => library.is_system || (library.has_folder && foldered <= 1);

  async function shift(id: number, direction: "up" | "down") {
    try {
      await shiftLibrary(id, direction);
      await reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    }
  }

  function showInWatchlist(id: number) {
    focusLibraryInWatchlist(id);
    window.location.hash = viewToHash({ page: "dashboard" });
  }

  async function makeDefault(id: number) {
    try {
      await makeLibraryDefault(id);
      await reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <section className={`libraries-page${embedded ? " embedded" : ""}`}>
      {/* The title above is the app's own header (it names the page from the
          navigation); a second one here printed "Libraries" twice. */}
      <div className="library-intro">
        {embedded ? <span /> : <p className="muted">{t("libraries.intro")}</p>}
        <div className="library-intro-actions">
          <button className="button secondary" disabled={refreshing !== null} onClick={() => void refresh("all")}>
            {refreshing === "all" ? t("libraries.refreshing") : t("libraries.refreshAll")}
          </button>
          {shokoMode && (
            <button className="button secondary" disabled={!shoko?.reachable} onClick={() => setMatching(true)}>
              {t("libraries.matchShoko")}
            </button>
          )}
          {/* The wizard has Add library only: one library per folder is the whole question there, and a second
              button that adds several at once reads like the same thing twice (the user, build 254). */}
          {!embedded && (
            <button className="button secondary" onClick={() => setChoosingParent(true)}>
              {t("libraries.addFromFolder")}
            </button>
          )}
          {!embedded && (
            <button className="button secondary" onClick={() => setImporting(true)}>
              {t("import.open")}
            </button>
          )}
          <button className="button primary" onClick={() => setEditing("new")}>
            {t("libraries.add")}
          </button>
        </div>
      </div>

      {refreshed && (
        <div className="detail-panel library-refresh-result">
          <div className="status-line" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{t("libraries.refreshTitle")}</h2>
            <button className="button secondary" onClick={() => setRefreshed(null)}>{t("libraries.refreshDismiss")}</button>
          </div>
          {refreshed.map((result) => (
            <div key={result.library_id}>
              <strong>{result.name}</strong>{" "}
              <small className="muted">{t("libraries.refreshChecked").replace("{n}", String(result.checked))}</small>
              {!refreshHasNews(result) ? (
                <p className="muted library-refresh-line">{t("libraries.refreshQuiet")}</p>
              ) : (
                <ul className="library-refresh-list">
                  {result.linked.length > 0 && <li>{t("libraries.refreshLinked").replace("{names}", result.linked.join(", "))}</li>}
                  {result.moved_in.length > 0 && <li>{t("libraries.refreshMovedIn").replace("{names}", result.moved_in.join(", "))}</li>}
                  {result.moved_out.length > 0 && <li>{t("libraries.refreshMovedOut").replace("{names}", result.moved_out.join(", "))}</li>}
                  {result.missing.length > 0 && <li className="reasons">{t("libraries.refreshMissing").replace("{names}", result.missing.join(", "))}</li>}
                  {result.unknown_folders.length > 0 && (
                    <li>
                      {t("libraries.refreshUnknown").replace("{names}", result.unknown_folders.join(", "))}{" "}
                      {!embedded && (
                        <button className="button secondary" onClick={() => setImporting(true)}>
                          {t("import.open")}
                        </button>
                      )}
                    </li>
                  )}
                  {(result.files_found > 0 || result.files_gone > 0) && <li>{t("libraries.refreshFiles").replace("{found}", String(result.files_found)).replace("{gone}", String(result.files_gone))}</li>}
                  {result.problems.map((problem) => <li className="reasons" key={problem}>{problem}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {shokoMode && shoko && !shoko.reachable && (
        <p className="error-message">{t("libraries.shokoUnreachable")}</p>
      )}

      <ul className="library-list">
        {[...libraries].filter((library) => !embedded || !library.is_system).sort((a, b) => Number(a.is_system) - Number(b.is_system)).map((library) => {
          const warning = shokoWarning(library, shoko, shokoMode);
          const relations = folderRelations(library, libraries);
          const own = libraries.filter((l) => !l.is_system);
          const place = own.findIndex((l) => l.id === library.id);
          const canUp = place > 0 && !library.is_default && !own[place - 1].is_default;
          const canDown = place >= 0 && place < own.length - 1 && !library.is_default;
          const shokoFolder =
            shokoMode && shoko?.reachable ? shoko.folders.find((f) => f.id === library.shoko_folder_id) ?? null : null;
          return (
            <Fragment key={library.id}>
            {/* The one special library gets its own heading: it has no folder, so it is
                not like the rest and nothing below it applies to it. */}
            {library.is_system && (
              <li className="library-special-head">
                <h2>{t("libraries.specialTitle")}</h2>
                <p className="muted">{t("libraries.specialHint")}</p>
              </li>
            )}
            <li className="library-row">
              <div className="library-order">
                {!library.is_system && (
                  <>
                    <button className="button secondary" disabled={!canUp} aria-label={t("libraries.moveUp")} title={t("libraries.moveUp")} onClick={() => void shift(library.id, "up")}>▲</button>
                    <button className="button secondary" disabled={!canDown} aria-label={t("libraries.moveDown")} title={t("libraries.moveDown")} onClick={() => void shift(library.id, "down")}>▼</button>
                  </>
                )}
              </div>
              <div className="library-main">
                <strong>
                  {library.key ? t(`libraries.system.${library.key}`) : library.name}
                  {library.is_default && <span className="chip sev-info library-badge">{t("libraries.default")}</span>}
                  
                </strong>
                {library.has_folder ? (
                  <code className="library-folder">
                    {shokoFolder ? t("libraries.folderAniflow").replace("{path}", library.folder) : library.folder}
                  </code>
                ) : (
                  <small className="muted">{t("libraries.noFolder")}</small>
                )}
                {shokoFolder?.path && (
                  <code className="library-folder">{t("libraries.folderShoko").replace("{path}", shokoFolder.path)}</code>
                )}
                {/* Its own row, so it is always in the same place whatever the line above it says. */}
                {!embedded && library.anime_count > 0 && (
                  <button className="library-link" onClick={() => showInWatchlist(library.id)}>
                    {t("libraries.showInWatchlist")}
                  </button>
                )}
                {/* Both numbers, so a gap between the anime AniFlow has and the folders really
                    there shows without opening anything. */}
                <small className="muted">
                  {library.exists && library.has_folder
                    ? t("libraries.animeDisk")
                        .replace("{n}", String(library.anime_count))
                        .replace("{m}", String(library.anime_with_folder))
                        .replace("{f}", String(library.folders_on_disk))
                    : t("libraries.animeCount").replace("{n}", String(library.anime_count))}
                </small>
                {/* What the files cost and what is left (build 233), on a line of its own under the counts. */}
                {library.exists && library.has_folder && library.free_bytes !== null ? (
                  <small className={"library-space" + (library.free_bytes < LOW_SPACE_BYTES ? " low" : "")}>
                    {t(library.used_bytes > 0 ? "libraries.space" : "libraries.spaceEmpty")
                      .replace("{used}", formatBytes(library.used_bytes))
                      .replace("{free}", formatBytes(library.free_bytes))}
                  </small>
                ) : null}
                {relations.shares.length > 0 && (
                  <small className="reasons">{t("libraries.sharesFolder").replace("{names}", relations.shares.join(", "))}</small>
                )}
                {relations.inside.length > 0 && (
                  <small className="muted">{t("libraries.insideFolder").replace("{names}", relations.inside.join(", "))}</small>
                )}
                {relations.contains.length > 0 && (
                  <small className="muted">{t("libraries.containsFolder").replace("{names}", relations.contains.join(", "))}</small>
                )}
                {!library.exists && <small className="reasons">{t("libraries.folderMissing")}</small>}
                {library.exists && !library.writable && <small className="reasons">{t("libraries.folderReadOnly")}</small>}
                {warning && <small className="reasons">{t(`libraries.shokoWarning.${warning}`)}</small>}
                <div className="library-tags">
                  {library.fits_adult === "adult" && <span className="chip sev-notice">{t("libraries.adult.adult")}</span>}
                  {library.fits_season && <span className="chip sev-notice">{t(`libraries.season.${library.fits_season}`)}</span>}
                </div>
              </div>
              {/* The same five buttons on every card, in the same columns, so they
                  line up from row to row. One that cannot do anything here is greyed
                  out, not left out. Remove - red - is furthest from the ones pressed
                  most, so it is not pressed by accident. */}
              <div className="library-actions">
                {/* Upcoming can be neither removed, made the default nor refreshed, so those three
                    are left out there; empty cells keep Move anime and Edit in their columns. */}
                {library.is_system ? (
                  <>
                    <span aria-hidden="true" />
                    <span aria-hidden="true" />
                    <span aria-hidden="true" />
                  </>
                ) : (
                  <>
                    {library.anime_count > 0 ? (
                      <button className="button danger" disabled={cannotRemove(library)} title={cannotRemove(library) ? t("libraries.removeLast") : undefined} onClick={() => setRemoving(library)}>
                        {t("libraries.remove")}
                      </button>
                    ) : (
                      <button
                        className="button danger"
                        disabled={cannotRemove(library)}
                        title={cannotRemove(library) ? t("libraries.removeLast") : undefined}
                        onBlur={() => setConfirming(null)}
                        onClick={() => (confirming === library.id ? void remove(library.id) : setConfirming(library.id))}
                      >
                        {confirming === library.id ? t("libraries.confirmRemove") : t("libraries.remove")}
                      </button>
                    )}
                    <button className="button secondary" disabled={library.is_default} onClick={() => void makeDefault(library.id)}>
                      {t("libraries.makeDefault")}
                    </button>
                    <button className="button secondary" disabled={refreshing !== null} onClick={() => void refresh(library.id)}>
                      {refreshing === library.id ? t("libraries.refreshing") : t("libraries.refresh")}
                    </button>
                  </>
                )}
                {!embedded && (
                  <button className="button secondary" disabled={library.anime_count === 0} onClick={() => setMovingFrom(library)}>
                    {t("libraries.moveAnime")}
                  </button>
                )}
                <button className="button secondary" onClick={() => setEditing(library)}>
                  {t("libraries.edit")}
                </button>
              </div>
            </li>
            </Fragment>
          );
        })}
      </ul>

      {removing && (
        <LibraryRemoveDialog
          library={removing}
          libraries={libraries}
          onClose={() => setRemoving(null)}
          onDone={() => void reload()}
        />
      )}

      {movingFrom && (
        <LibraryMoveDialog
          candidates={anime
            .filter((a) => a.library_id === movingFrom.id)
            .map((a) => ({ id: a.id, name: a.display_name }))}
          initiallySelected={anime.filter((a) => a.library_id === movingFrom.id).map((a) => a.id)}
          libraries={libraries}
          currentLibraryId={movingFrom.id}
          onClose={() => setMovingFrom(null)}
          onDone={() => void reload()}
        />
      )}

      {matching && shoko && (
        <ShokoFoldersDialog
          shoko={shoko}
          libraries={libraries}
          onAdd={setPreset}
          onClose={() => setMatching(false)}
        />
      )}

      {choosingParent && (
        <FolderPickerDialog
          onClose={() => setChoosingParent(false)}
          onPick={(path) => {
            setChoosingParent(false);
            setParent(path);
          }}
        />
      )}

      {parent && (
        <SubfoldersDialog
          parent={parent}
          libraries={libraries}
          onAdd={setPreset}
          onClose={() => setParent(null)}
        />
      )}

      {importing && <ImportFoldersDialog onClose={() => setImporting(false)} onDone={() => void reload()} />}

      {preset && (
        <LibraryDialog
          library={null}
          preset={preset}
          shoko={shokoMode ? shoko : null}
          onClose={() => setPreset(null)}
          onSaved={() => {
            setPreset(null);
            void reload();
          }}
        />
      )}

      {editing && (
        <LibraryDialog
          library={editing === "new" ? null : editing}
          shoko={shokoMode ? shoko : null}
          onClose={() => setEditing(null)}
          onSaved={(folderChanged) => {
            // Pointing a library at another folder: look at the disk again at once. A refresh
            // changes only the database, so it is safe to do for the user.
            const changed = folderChanged && editing !== "new" && editing !== null && editing.anime_count > 0 ? editing : null;
            setEditing(null);
            if (changed) void refresh(changed.id);
            else void reload();
          }}
        />
      )}
    </section>
  );
}

const SEASON = ["current", "past"];

function LibraryDialog({
  library,
  preset,
  shoko,
  onClose,
  onSaved,
}: {
  library: Library | null;
  /** A new library's starting values (from a Shoko folder); the user confirms them. */
  preset?: LibraryPreset;
  shoko: ShokoImportFolders | null;
  onClose: () => void;
  onSaved: (folderChanged?: boolean) => void;
}) {
  useLockedPageScroll();
  const { show } = useToast();
  /* Upcoming has no folder, so there is nothing to choose or save for it. */
  const hasFolder = library ? library.has_folder : true;
  const [name, setName] = useState(library?.name ?? preset?.name ?? "");
  const [folder, setFolder] = useState(library?.folder ?? preset?.folder ?? "");
  const [picking, setPicking] = useState(false);
  const [adult, setAdult] = useState(library ? library.fits_adult === "adult" : preset?.adult ?? false);
  const [season, setSeason] = useState(library ? library.fits_season ?? "" : preset?.season ?? "");
  const [shokoId, setShokoId] = useState<string>(library?.shoko_folder_id?.toString() ?? preset?.shokoFolderId?.toString() ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    const draft: LibraryDraft = {
      name: name.trim(),
      folder,
      fits_adult: adult ? "adult" : null,
      fits_season: season || null,
      shoko_folder_id: shokoId ? Number(shokoId) : null,
    };
    try {
      if (library) {
        await updateLibrary(library.id, {
          ...draft,
          // A built-in library keeps its name; the server refuses a rename.
          name: library.is_system ? undefined : draft.name,
          folder: hasFolder ? draft.folder : undefined,
          shoko_folder_id: draft.shoko_folder_id ?? undefined,
          clear_shoko_folder: draft.shoko_folder_id === null,
        });
      } else {
        await createLibrary(draft);
      }
      onSaved(Boolean(library) && hasFolder && folder !== library?.folder);
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      setError(message);
      show(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide">
        <header className="dialog-head">
          <h2>{library ? t("libraries.editTitle") : t("libraries.addTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>

        <label className="field">
          {t("libraries.name")}
          <input
            value={name}
            maxLength={80}
            disabled={library?.is_system}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("libraries.namePlaceholder")}
          />
        </label>

        {hasFolder && <div className="field library-field">
          {t("libraries.folder")}
          <div className="library-folder-line">
            <code className="library-folder library-folder-box" aria-readonly="true">{folder || "—"}</code>
            <button
              className="button secondary library-folder-button"
              onClick={() => setPicking(true)}
              aria-label={t("libraries.chooseFolder")}
              title={t("libraries.chooseFolder")}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              </svg>
            </button>
          </div>
        </div>}

        {hasFolder && (
          <div className="library-fits-grid">
            <FitsSelect label={t("libraries.seasonLabel")} value={season} values={SEASON} name={(v) => t(`libraries.season.${v}`)} onChange={setSeason} />
          </div>
        )}

        <label className="check library-adult-check">
          <input type="checkbox" checked={adult} onChange={(event) => setAdult(event.target.checked)} />
          {t("libraries.adultCheck")}
        </label>

        {hasFolder && shoko?.reachable && (
          <label className="field library-field">
            {t("libraries.shokoFolder")}
            <select value={shokoId} onChange={(event) => setShokoId(event.target.value)}>
              <option value="">{t("libraries.shokoNone")}</option>
              {shoko.folders.map((f) => (
                <option key={String(f.id)} value={String(f.id)}>
                  {f.name} — {f.path}
                </option>
              ))}
            </select>
          </label>
        )}

        {error && <p className="error-message">{error}</p>}
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.cancel")}
          </button>
          <button className="button primary" disabled={busy || (hasFolder && !folder) || (!library?.is_system && !name.trim())} onClick={() => void save()}>
            {t("libraries.save")}
          </button>
        </div>
      </div>
    </div>
    {picking && (
      <FolderPickerDialog
        start={folder || undefined}
        onClose={() => setPicking(false)}
        onPick={(path) => {
          setFolder(path);
          setPicking(false);
        }}
      />
    )}
    </>
  );
}

function FitsSelect({
  label,
  value,
  values,
  name,
  onChange,
}: {
  label: string;
  value: string;
  values: string[];
  name: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{t("libraries.doesNotMatter")}</option>
        {values.map((v) => (
          <option key={v} value={v}>
            {name(v)}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Choose a folder: a tree of what AniFlow's container can see.
 *
 * Clicking a folder selects it and opens it (or closes it again), whether or
 * not it has anything inside; Select takes the highlighted one. Nothing above
 * the browse root is ever offered - the server decides what is listed.
 */
function FolderPickerDialog({
  start,
  onPick,
  onClose,
}: {
  start?: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  useLockedPageScroll();
  const [rootPath, setRootPath] = useState<string | null>(null);
  const [children, setChildren] = useState<Record<string, FolderBrowsing["entries"]>>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(start ?? null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (path?: string) => {
    const view = await browseFolders(path);
    setChildren((current) => ({ ...current, [view.path]: view.entries }));
    return view;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const root = await load();
        if (cancelled) return;
        setRootPath(root.path);
        if (!start) return;
        // Open the way down to the folder that is already chosen.
        const opened = new Set<string>();
        for (const path of pathChain(root.path, start)) {
          try {
            await load(path);
          } catch {
            break;
          }
          opened.add(path);
        }
        if (!cancelled) setOpen(opened);
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));
      }
    })();
    return () => {
      cancelled = true;
    };
    // Only the first look: the tree is the user's from then on.
  }, []);

  async function click(path: string) {
    setSelected(path);
    if (open.has(path)) {
      setOpen((current) => {
        const next = new Set(current);
        next.delete(path);
        return next;
      });
      return;
    }
    setOpen((current) => new Set(current).add(path));
    if (children[path] === undefined) {
      try {
        await load(path);
      } catch {
        setChildren((current) => ({ ...current, [path]: [] }));
      }
    }
  }

  function level(parent: string, depth: number): ReactNode {
    const entries = children[parent];
    if (!entries) return null;
    if (entries.length === 0 && depth > 0) {
      return (
        <p className="muted folder-tree-empty" style={{ paddingLeft: 38 + depth * 18 }}>
          {t("libraries.noSubfolders")}
        </p>
      );
    }
    return entries.map((entry) => {
      const isOpen = open.has(entry.path);
      return (
        <div key={entry.path}>
          <button
            className={`folder-tree-row${selected === entry.path ? " is-selected" : ""}`}
            style={{ paddingLeft: 10 + depth * 18 }}
            aria-expanded={isOpen}
            onClick={() => void click(entry.path)}
          >
            <span className={`folder-tree-chevron${isOpen ? " is-open" : ""}`} aria-hidden="true">
              ›
            </span>
            <span className="folder-tree-name">{entry.name}</span>
            {!entry.writable && <small className="reasons">{t("libraries.folderReadOnly")}</small>}
          </button>
          {isOpen && level(entry.path, depth + 1)}
        </div>
      );
    });
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog">
        <header className="dialog-head">
          <h2>{t("libraries.chooseFolderTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        {error && <p className="error-message">{error}</p>}
        <div className="folder-tree">{rootPath !== null && level(rootPath, 0)}</div>
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.cancel")}
          </button>
          <button className="button primary" disabled={!selected} onClick={() => selected && onPick(selected)}>
            {t("libraries.select")}
          </button>
        </div>
      </div>
    </div>
  );
}

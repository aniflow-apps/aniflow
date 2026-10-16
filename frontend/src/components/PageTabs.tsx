/**
 * The tab row of a page that is split into parts (build 258): the Shoko Server and AniDB pages.
 *
 * The same look as the Settings tabs (`settings-tabs`), because it is the same idea - along the top, the left
 * being the navigation already. The page decides which tab is shown (from the URL's section) and this only draws
 * the row and says which was pressed.
 */
export interface PageTab<T extends string> {
  id: T;
  label: string;
}

export function PageTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: PageTab<T>[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="settings-tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          className={`settings-tab ${active === tab.id ? "active" : ""}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

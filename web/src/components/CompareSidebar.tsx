import type { AppView, DiffEntity, DiffStatus, SemanticDiff } from "../types";

type Props = {
  diff: SemanticDiff;
  view: AppView;
  siteFilter: string;
  query: string;
  selectedId: string | null;
  onlyChanges: boolean;
  onQuery: (value: string) => void;
  onSelect: (entity: DiffEntity) => void;
  onOnlyChanges: (value: boolean) => void;
};

const KIND_TITLE: Record<DiffEntity["kind"], string> = {
  site: "Sitios",
  device: "Dispositivos",
  network: "VLANs / redes",
  cluster: "Clústeres",
  template: "Templates",
  release: "Releases Helm",
  values: "Values",
};

const STATUS_RANK: Record<DiffStatus, number> = {
  added: 0,
  removed: 1,
  modified: 2,
  unchanged: 3,
};

function kindsForView(view: AppView, siteFilter: string): DiffEntity["kind"][] {
  if (siteFilter === "all" && view === "network") return ["site", "network"];
  if (view === "kubernetes") return ["template", "cluster", "release", "values"];
  return ["device", "network", "cluster"];
}

export function CompareSidebar({
  diff,
  view,
  siteFilter,
  query,
  selectedId,
  onlyChanges,
  onQuery,
  onSelect,
  onOnlyChanges,
}: Props) {
  const needle = query.trim().toLowerCase();
  const kinds = kindsForView(view, siteFilter);
  const visible = diff.entities.filter((item) => {
    if (!kinds.includes(item.kind)) return false;
    if (onlyChanges && item.status === "unchanged") return false;
    if (!needle) return true;
    return `${item.label} ${item.id} ${item.kind}`.toLowerCase().includes(needle);
  });
  const grouped = new Map<DiffEntity["kind"], DiffEntity[]>();
  for (const item of visible) {
    const list = grouped.get(item.kind) ?? [];
    list.push(item);
    grouped.set(item.kind, list);
  }
  for (const list of grouped.values()) {
    list.sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status] || a.id.localeCompare(b.id));
  }

  return (
    <aside className="sidebar">
      <label className="search">
        <span>Buscar en el diff</span>
        <input
          id="compare-search"
          name="compare-search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="entidad, ruta…"
          autoComplete="off"
        />
      </label>
      <p className="count">
        +{diff.summary.added} / −{diff.summary.removed} / ~{diff.summary.modified}
        <span className="muted"> · {diff.summary.unchanged} igual</span>
      </p>
      <label className="diff-filter sidebar-filter">
        <input
          type="checkbox"
          checked={onlyChanges}
          onChange={(event) => onOnlyChanges(event.target.checked)}
        />{" "}
        solo cambios
      </label>
      <nav className="device-nav">
        {kinds.map((kind) => {
          const members = grouped.get(kind) ?? [];
          if (members.length === 0) return null;
          return (
            <section key={kind}>
              <h2>{KIND_TITLE[kind]}</h2>
              <ul>
                {members.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`${selectedId === item.id ? "is-active" : ""} status-${item.status}`}
                      onClick={() => onSelect(item)}
                    >
                      <span className="name">{item.label}</span>
                      <span className="meta">
                        <StatusMark status={item.status} /> {item.site || "global"}
                        {item.changes.length ? ` · ${item.changes.length} rutas` : ""}
                        <ChildBadge counts={item.child_counts} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        {visible.length === 0 ? <p className="empty">Nada coincide con el filtro.</p> : null}
      </nav>
    </aside>
  );
}

function ChildBadge({ counts }: { counts: DiffEntity["child_counts"] }) {
  if (!counts) return null;
  const added = counts.added || 0;
  const removed = counts.removed || 0;
  const modified = counts.modified || 0;
  if (!added && !removed && !modified) return null;
  return (
    <span className="diff-badge inline">
      {added ? <span className="add">+{added}</span> : null}
      {removed ? <span className="del">−{removed}</span> : null}
      {modified ? <span className="mod">~{modified}</span> : null}
    </span>
  );
}

function StatusMark({ status }: { status: DiffStatus }) {
  if (status === "added") return <span className="diff-mark added">+</span>;
  if (status === "removed") return <span className="diff-mark removed">−</span>;
  if (status === "modified") return <span className="diff-mark modified">~</span>;
  return <span className="diff-mark unchanged">=</span>;
}

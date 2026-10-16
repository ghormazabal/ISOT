import { KIND_LABELS } from "../layout";
import type {
  AppView,
  ClusterIndexEntry,
  DeviceIndexEntry,
  SiteFilter,
  SiteIndexEntry,
  TemplateIndexEntry,
} from "../types";

type Props = {
  view: AppView;
  siteFilter: SiteFilter;
  sites: SiteIndexEntry[];
  devices: DeviceIndexEntry[];
  clusters: ClusterIndexEntry[];
  templates: TemplateIndexEntry[];
  query: string;
  selectedId: string | null;
  onQuery: (value: string) => void;
  onSelectSite: (id: string) => void;
  onSelectDevice: (site: string, hostname: string) => void;
  onSelectCluster: (site: string, name: string) => void;
  onSelectTemplate: (site: string, name: string) => void;
};

export function Sidebar({
  view,
  siteFilter,
  sites,
  devices,
  clusters,
  templates,
  query,
  selectedId,
  onQuery,
  onSelectSite,
  onSelectDevice,
  onSelectCluster,
  onSelectTemplate,
}: Props) {
  const needle = query.trim().toLowerCase();
  const match = (...parts: Array<string | null | undefined>) =>
    !needle || parts.filter(Boolean).join(" ").toLowerCase().includes(needle);

  if (siteFilter === "all" && view === "network") {
    const filtered = sites.filter((item) => match(item.id, item.name, item.description));
    return (
      <aside className="sidebar">
        <label className="search">
          <span>Buscar</span>
          <input
            id="device-search"
            name="device-search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="sitio…"
            autoComplete="off"
          />
        </label>
        <p className="count">
          {filtered.length} / {sites.length} sitios
        </p>
        <nav className="device-nav">
          <section>
            <h2>Sitios</h2>
            <ul>
              {filtered.map((site) => (
                <li key={site.id}>
                  <button
                    type="button"
                    className={selectedId === `site:${site.id}` ? "is-active" : ""}
                    onClick={() => onSelectSite(site.id)}
                  >
                    <span className="name">{site.name}</span>
                    <span className="meta">
                      {site.device_count} equipos · {site.cluster_count} clústeres
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </nav>
      </aside>
    );
  }

  if (view === "kubernetes") {
    const filteredTemplates = templates.filter((item) =>
      match(item.name, item.distro, item.version, item.cni, item.site),
    );
    const filteredClusters = clusters.filter(
      (item) => item.kind === "kubernetes" && match(item.name, item.distro, item.template_ref, item.version, item.site),
    );
    return (
      <aside className="sidebar">
        <label className="search">
          <span>Buscar</span>
          <input
            id="device-search"
            name="device-search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="template, clúster…"
            autoComplete="off"
          />
        </label>
        <nav className="device-nav">
          <section>
            <h2>Templates</h2>
            <ul>
              {filteredTemplates.map((item) => (
                <li key={`${item.site}:${item.name}`}>
                  <button
                    type="button"
                    className={selectedId === `template:${item.site}:${item.name}` ? "is-active" : ""}
                    onClick={() => onSelectTemplate(item.site, item.name)}
                  >
                    <span className="name">{item.name}</span>
                    <span className="meta">
                      {item.scope === "global" ? "global" : item.site} · {item.used_by.length} clúster(es)
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2>Clústeres</h2>
            <ul>
              {filteredClusters.map((item) => (
                <li key={`${item.site}:${item.name}`}>
                  <button
                    type="button"
                    className={selectedId === `cluster:${item.site}:${item.name}` ? "is-active" : ""}
                    onClick={() => onSelectCluster(item.site, item.name)}
                  >
                    <span className="name">{item.name}</span>
                    <span className="meta">
                      {item.site} · {item.distro ?? "—"} · {item.node_count} nodos
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </nav>
      </aside>
    );
  }

  const filtered = devices.filter((device) =>
    match(device.hostname, device.role, device.ip, device.kind, device.cluster_ref, device.site),
  );
  const groups = new Map<string, DeviceIndexEntry[]>();
  for (const device of filtered) {
    const key = device.kind || "otros";
    const list = groups.get(key) ?? [];
    list.push(device);
    groups.set(key, list);
  }

  return (
    <aside className="sidebar">
      <label className="search">
        <span>Buscar</span>
        <input
          id="device-search"
          name="device-search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="hostname, rol, IP…"
          autoComplete="off"
        />
      </label>
      <p className="count">
        {filtered.length} / {devices.length} dispositivos
      </p>
      <nav className="device-nav">
        {[...groups.entries()].map(([kind, members]) => (
          <section key={kind}>
            <h2>{KIND_LABELS[kind] || kind}</h2>
            <ul>
              {members.map((device) => (
                <li key={`${device.site}:${device.hostname}`}>
                  <button
                    type="button"
                    className={`${device.site}:${device.hostname}` === selectedId ? "is-active" : ""}
                    onClick={() => onSelectDevice(device.site, device.hostname)}
                  >
                    <span className="name">{device.hostname}</span>
                    <span className="meta">
                      {device.role ?? "—"} · {device.ip ?? "sin-ip"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {filtered.length === 0 ? <p className="empty">Ningún dispositivo coincide.</p> : null}
      </nav>
    </aside>
  );
}

import type {
  ClusterDetail,
  DeviceDetail,
  DiffEntity,
  Selection,
  SiteIndexEntry,
  TemplateDetail,
} from "../types";
import { formatValue } from "./ValuesTree";

type Props = {
  selection: Selection | null;
  sites: SiteIndexEntry[];
  device: DeviceDetail | null;
  cluster: ClusterDetail | null;
  template: TemplateDetail | null;
  loading: boolean;
  error: string | null;
  onOpenDevice: (site: string, hostname: string) => void;
  onOpenCluster: (site: string, name: string) => void;
  onOpenTemplate: (site: string, name: string) => void;
  onOpenSite: (id: string) => void;
  onShowExplorer?: () => void;
  onShowDiff?: () => void;
  diffEntity?: DiffEntity | null;
  onSelectDiffEntity?: (id: string) => void;
};

function dash(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function splitUsedBy(value: string): { site: string; name: string } {
  const idx = value.indexOf("/");
  if (idx === -1) return { site: "", name: value };
  return { site: value.slice(0, idx), name: value.slice(idx + 1) };
}

export function DetailPanel({
  selection,
  sites,
  device,
  cluster,
  template,
  loading,
  error,
  onOpenDevice,
  onOpenCluster,
  onOpenTemplate,
  onOpenSite,
  onShowExplorer,
  onShowDiff,
  diffEntity = null,
  onSelectDiffEntity,
}: Props) {
  const selectedSite = selection?.type === "site" ? sites.find((item) => item.id === selection.id) : null;
  return (
    <section className="detail">
      <header>
        <h2>Detalle</h2>
        <p>{diffEntity ? "Diff entre refs de git" : "YAML cargado bajo demanda"}</p>
      </header>
      {loading ? <p className="muted">Cargando…</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {!selection && !diffEntity && !loading ? (
        <p className="placeholder">
          Selecciona un sitio, dispositivo, clúster o template. El índice ya está en memoria; el YAML se
          pedirá ahora.
        </p>
      ) : null}
      {diffEntity ? <DiffEntityBody entity={diffEntity} onSelectChild={onSelectDiffEntity} /> : null}
      {!diffEntity && selectedSite ? <SiteBody site={selectedSite} onOpenSite={onOpenSite} /> : null}
      {!diffEntity && selection?.type === "device" && device ? (
        <DeviceBody
          device={device}
          onOpenDevice={onOpenDevice}
          onOpenCluster={onOpenCluster}
        />
      ) : null}
      {!diffEntity && selection?.type === "cluster" && cluster ? (
        <ClusterBody
          cluster={cluster}
          onOpenDevice={onOpenDevice}
          onOpenTemplate={onOpenTemplate}
          onShowDiff={onShowDiff}
        />
      ) : null}
      {!diffEntity && selection?.type === "template" && template ? (
        <TemplateBody template={template} onOpenCluster={onOpenCluster} onShowExplorer={onShowExplorer} />
      ) : null}
    </section>
  );
}

function SiteBody({ site, onOpenSite }: { site: SiteIndexEntry; onOpenSite: (id: string) => void }) {
  return (
    <div>
      <h1>{site.name}</h1>
      <p className="mono muted">{site.path}</p>
      <dl className="facts">
        <div>
          <dt>Id</dt>
          <dd className="mono">{site.id}</dd>
        </div>
        <div>
          <dt>Equipos</dt>
          <dd>{site.device_count}</dd>
        </div>
        <div>
          <dt>Clústeres</dt>
          <dd>{site.cluster_count}</dd>
        </div>
        <div>
          <dt>Espacios</dt>
          <dd>{site.address_spaces.length ? site.address_spaces.join(", ") : "—"}</dd>
        </div>
      </dl>
      <p>{site.description || "—"}</p>
      <h3>Enlaces entre sitios</h3>
      {site.inter_site_links.length === 0 ? (
        <p className="muted">ninguno documentado</p>
      ) : (
        <ul className="member-list">
          {site.inter_site_links.map((link) => (
            <li key={`${link.device}-${link.peer_site}-${link.peer_device}`}>
              <span className="mono">{link.device}</span> →{" "}
              <button type="button" className="text-link" onClick={() => onOpenSite(link.peer_site)}>
                {link.peer_site}/{link.peer_device}
              </button>
              {link.vlan != null ? <span className="muted"> · vlan {String(link.vlan)}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DeviceBody({
  device,
  onOpenDevice,
  onOpenCluster,
}: {
  device: DeviceDetail;
  onOpenDevice: (site: string, hostname: string) => void;
  onOpenCluster: (site: string, name: string) => void;
}) {
  const data = device.data;
  const networking = Array.isArray(data.networking) ? data.networking : [];
  const clusterRef = data.cluster_ref;
  const site = device.index.site;
  return (
    <div>
      <h1>{dash(data.hostname)}</h1>
      <dl className="facts">
        <div>
          <dt>Sitio</dt>
          <dd className="mono">{site}</dd>
        </div>
        <div>
          <dt>Rol</dt>
          <dd>{dash(data.role)}</dd>
        </div>
        <div>
          <dt>Clúster</dt>
          <dd>
            {clusterRef ? (
              <button
                type="button"
                className="text-link"
                onClick={() => onOpenCluster(device.index.cluster_ref_site || site, String(clusterRef))}
              >
                {String(clusterRef)}
              </button>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div>
          <dt>OS</dt>
          <dd>{dash(data.os)}</dd>
        </div>
        <div>
          <dt>Archivo</dt>
          <dd className="mono">{device.index.path}</dd>
        </div>
      </dl>
      <h3>Interfaces</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Interfaz</th>
              <th>IP</th>
              <th>VLAN</th>
              <th>MAC</th>
              <th>connected_to</th>
            </tr>
          </thead>
          <tbody>
            {networking.map((iface, index) => {
              const peer = iface.connected_to?.device;
              const peerSite = iface.connected_to?.site || site;
              return (
                <tr key={`${iface.interface ?? iface.name ?? index}`}>
                  <td className="mono">{dash(iface.interface ?? iface.name)}</td>
                  <td className="mono">{dash(iface.ip)}</td>
                  <td>{dash(iface.vlan)}</td>
                  <td className="mono">{dash(iface.mac)}</td>
                  <td>
                    {peer ? (
                      <>
                        <button type="button" className="text-link" onClick={() => onOpenDevice(peerSite, peer)}>
                          {peerSite !== site ? `${peerSite}/` : ""}
                          {peer}
                        </button>
                        {iface.connected_to?.port ? <span className="muted"> {iface.connected_to.port}</span> : null}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ClusterBody({
  cluster,
  onOpenDevice,
  onOpenTemplate,
  onShowDiff,
}: {
  cluster: ClusterDetail;
  onOpenDevice: (site: string, hostname: string) => void;
  onOpenTemplate: (site: string, name: string) => void;
  onShowDiff?: () => void;
}) {
  const data = cluster.data;
  const templateRef = data.template_ref ? String(data.template_ref) : "";
  const nodes = Array.isArray(data.nodes) ? (data.nodes as Array<Record<string, unknown>>) : [];
  const nads = Array.isArray(data.nads) ? (data.nads as Array<Record<string, unknown>>) : [];
  const metallb = (data.metallb as Record<string, unknown> | undefined) || {};
  const pools = Array.isArray(metallb.pools) ? (metallb.pools as Array<Record<string, unknown>>) : [];
  const byRole = new Map<string, string[]>();
  for (const node of nodes) {
    const hostname = String(node.hostname ?? node.name ?? "");
    const roles = Array.isArray(node.roles) ? node.roles.map(String) : ["(sin rol)"];
    for (const role of roles) {
      const list = byRole.get(role) ?? [];
      list.push(hostname);
      byRole.set(role, list);
    }
  }
  return (
    <div>
      <h1>{cluster.name}</h1>
      <p className="mono muted">{cluster.path}</p>
      <dl className="facts">
        <div>
          <dt>Sitio</dt>
          <dd className="mono">{cluster.site}</dd>
        </div>
        <div>
          <dt>Kind</dt>
          <dd>{dash(data.kind)}</dd>
        </div>
        <div>
          <dt>Distro</dt>
          <dd>{dash(data.distro)}</dd>
        </div>
        <div>
          <dt>Versión</dt>
          <dd>{dash(data.version)}</dd>
        </div>
        <div>
          <dt>Template</dt>
          <dd>
            {templateRef ? (
              <button
                type="button"
                className="text-link"
                onClick={() => onOpenTemplate(cluster.template_site || "global", templateRef)}
              >
                {templateRef}
              </button>
            ) : (
              dash(data.template_note)
            )}
          </dd>
        </div>
        <div>
          <dt>pod CIDR</dt>
          <dd className="mono">{dash(data.pod_cidr)}</dd>
        </div>
        <div>
          <dt>svc CIDR</dt>
          <dd className="mono">{dash(data.service_cidr)}</dd>
        </div>
      </dl>
      {byRole.size > 0 ? (
        <>
          <h3>Nodos por rol</h3>
          {[...byRole.entries()].map(([role, hostnames]) => (
            <p key={role}>
              <span className="muted">{role}: </span>
              {hostnames.map((hostname, index) => (
                <span key={hostname}>
                  {index > 0 ? ", " : null}
                  <button type="button" className="text-link" onClick={() => onOpenDevice(cluster.site, hostname)}>
                    {hostname}
                  </button>
                </span>
              ))}
            </p>
          ))}
        </>
      ) : (
        <>
          <h3>Miembros (índice)</h3>
          <ul className="member-list">
            {cluster.members.map((hostname) => (
              <li key={hostname}>
                <button type="button" className="text-link" onClick={() => onOpenDevice(cluster.site, hostname)}>
                  {hostname}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {templateRef ? (
        <p>
          <button type="button" className="text-link" onClick={onShowDiff}>
            Ver diferencias de values vs template
          </button>
        </p>
      ) : null}
      <h3>Overrides de values</h3>
      {Array.isArray(data.values_overrides) && (data.values_overrides as unknown[]).length > 0 ? (
        <ul className="member-list">
          {(data.values_overrides as Array<Record<string, unknown>>).map((item) => (
            <li key={String(item.release)}>
              <span className="mono">{String(item.release)}</span>
              <span className="muted"> · values_inline</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{templateRef ? "ningún override declarado" : "sin template"}</p>
      )}
      <h3>NADs</h3>
      {nads.length === 0 ? (
        <p className="muted">{dash(data.nads_note) === "—" ? "ninguno documentado" : dash(data.nads_note)}</p>
      ) : (
        <ul className="member-list">
          {nads.map((nad) => (
            <li key={`${nad.namespace}/${nad.name}`}>
              <span className="mono">
                {String(nad.namespace)}/{String(nad.name)}
              </span>{" "}
              <span className="muted">
                vlan {dash(nad.vlan)} · {dash(nad.subnet)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <h3>MetalLB</h3>
      {pools.length === 0 ? (
        <p className="muted">sin pools</p>
      ) : (
        <ul className="member-list">
          {pools.map((pool) => (
            <li key={String(pool.name)}>
              <strong>{String(pool.name)}</strong>{" "}
              <span className="mono muted">{dash(pool.subnet)}</span>
              <div className="mono">{Array.isArray(pool.addresses) ? pool.addresses.join(", ") : "—"}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TemplateBody({
  template,
  onOpenCluster,
  onShowExplorer,
}: {
  template: TemplateDetail;
  onOpenCluster: (site: string, name: string) => void;
  onShowExplorer?: () => void;
}) {
  const data = template.data;
  const cni = data.cni as Record<string, unknown> | undefined;
  return (
    <div>
      <h1>{template.name}</h1>
      <p className="mono muted">{template.path}</p>
      <dl className="facts">
        <div>
          <dt>Ámbito</dt>
          <dd>{template.site === "global" ? "global" : template.site}</dd>
        </div>
        <div>
          <dt>Versión</dt>
          <dd>{dash(data.version)}</dd>
        </div>
        <div>
          <dt>Distro</dt>
          <dd>{dash(data.distro)}</dd>
        </div>
        <div>
          <dt>K8s</dt>
          <dd className="mono">{dash(data.kubernetes_version)}</dd>
        </div>
        <div>
          <dt>CNI</dt>
          <dd>{dash(cni?.primary)}</dd>
        </div>
        <div>
          <dt>Nota</dt>
          <dd>{dash(data.note ?? data.description)}</dd>
        </div>
      </dl>
      <p>
        <button type="button" className="text-link" onClick={onShowExplorer}>
          Abrir explorador de charts y values
        </button>
      </p>
      <h3>Charts</h3>
      {(template.charts ?? []).length === 0 ? (
        <p className="muted">{dash(data.charts_note) === "—" ? "ningún Helm release documentado" : dash(data.charts_note)}</p>
      ) : (
        <ul className="member-list">
          {template.charts.map((chart) => (
            <li key={chart.name}>
              <span className="mono">{chart.name}</span>{" "}
              <span className="muted">
                {chart.version || "versión faltante"} · {chart.values_source}
              </span>
            </li>
          ))}
        </ul>
      )}
      <h3>Clústeres que lo usan</h3>
      {template.used_by.length === 0 ? (
        <p className="muted">Ningún clúster referencia este template.</p>
      ) : (
        <ul className="member-list">
          {template.used_by.map((item) => {
            const parsed = splitUsedBy(item);
            return (
              <li key={item}>
                <button
                  type="button"
                  className="text-link"
                  onClick={() => onOpenCluster(parsed.site || template.site, parsed.name)}
                >
                  {item}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function DiffEntityBody({
  entity,
  onSelectChild,
}: {
  entity: DiffEntity;
  onSelectChild?: (id: string) => void;
}) {
  const counts = entity.child_counts;
  const children = entity.children ?? [];
  return (
    <div>
      <h1>{entity.label}</h1>
      <p className="mono muted">
        {entity.kind} · {entity.site || "global"} · {entity.id}
      </p>
      <dl className="facts">
        <div>
          <dt>Estado</dt>
          <dd>
            {entity.status}
            {entity.inherited ? " (por hijos)" : ""}
          </dd>
        </div>
        <div>
          <dt>Rutas</dt>
          <dd>{entity.changes.length}</dd>
        </div>
        {counts && (counts.added || counts.removed || counts.modified) ? (
          <div>
            <dt>Hijos</dt>
            <dd>
              +{counts.added} −{counts.removed} ~{counts.modified}
            </dd>
          </div>
        ) : null}
      </dl>
      {children.length > 0 ? (
        <>
          <h3>Entidades hijas cambiadas</h3>
          <ul className="member-list">
            {children.map((child) => (
              <li key={child.id}>
                <span className={`diff-mark ${child.status}`}>
                  {child.status === "added" ? "+" : child.status === "removed" ? "−" : "~"}
                </span>{" "}
                {onSelectChild ? (
                  <button type="button" className="text-link" onClick={() => onSelectChild(child.id)}>
                    {child.label}
                  </button>
                ) : (
                  <span>{child.label}</span>
                )}
                <span className="muted">
                  {" "}
                  · {child.kind}
                  {child.site ? ` · ${child.site}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {entity.changes.length === 0 ? (
        <p className="muted">
          {entity.inherited ? "Sin diferencias propias; el cambio está en los hijos." : "Sin diferencias en el payload."}
        </p>
      ) : (
        <div className="table-wrap">
          <table className="diff-table">
            <thead>
              <tr>
                <th>Ruta</th>
                <th>Antes</th>
                <th>Después</th>
              </tr>
            </thead>
            <tbody>
              {entity.changes.map((row) => (
                <tr key={row.path} className={`diff-row ${row.status === "modified" ? "overridden" : row.status}`}>
                  <td className="mono">{row.path}</td>
                  <td className="mono">{row.status === "added" ? "—" : formatValue(row.before)}</td>
                  <td className="mono">{row.status === "removed" ? "—" : formatValue(row.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

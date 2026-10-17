import type { DiffStatus, EdgeSelection, Finding, LinkDetail, LinkEndpoint, LinkItem, LinkResponse } from "../types";
import { DASH, LINK_TYPE_LABEL, changeRows, dashValue, sideRows, vlanLabel, vlanNote } from "../links";

type Props = {
  edge: EdgeSelection;
  response: LinkResponse | null;
  fallback: LinkDetail | null;
  loading: boolean;
  error: string | null;
  findings: Finding[];
  compare?: boolean;
  onOpenEndpoint?: (endpoint: LinkEndpoint) => void;
  onClose?: () => void;
};

const STATUS_LABEL: Record<string, string> = {
  added: "agregada",
  removed: "quitada",
  modified: "modificada",
  unchanged: "sin cambios",
  missing: "sin datos",
};

function EndpointLink({ endpoint, onOpen }: { endpoint: LinkEndpoint; onOpen?: (endpoint: LinkEndpoint) => void }) {
  const meta = [endpoint.role, endpoint.cluster_ref ? `clúster ${endpoint.cluster_ref}` : null, endpoint.kind]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="link-endpoint" data-endpoint={endpoint.id}>
      {onOpen && !endpoint.remote ? (
        <button type="button" className="text-link link-endpoint-name" onClick={() => onOpen(endpoint)}>
          {endpoint.label}
        </button>
      ) : (
        <span className="link-endpoint-name">{endpoint.label}</span>
      )}
      {endpoint.proposed ? <span className="value-tag added">propuesto</span> : null}
      {endpoint.remote ? <span className="value-tag missing">remoto</span> : null}
      <span className="mono link-endpoint-meta">{meta || DASH}</span>
    </div>
  );
}

function LinkCard({ link, detail, index }: { link: LinkItem; detail: LinkDetail; index: number }) {
  const left = link.pair?.source.label ?? detail.source.label;
  const right = link.pair?.target.label ?? detail.target.label;
  const attributes = Object.entries(link.attributes).filter(
    ([key, value]) => !["mtu", "lacp"].includes(key) || (value !== null && value !== undefined),
  );
  return (
    <details className={`link-card link-${link.type}`} open={index < 2} data-link={link.label}>
      <summary>
        <span className="value-tag">{LINK_TYPE_LABEL[link.type]}</span> <span className="mono">{link.label}</span>
        {link.pair ? (
          <span className="muted">
            {" "}
            · {link.pair.source.label} ↔ {link.pair.target.label}
          </span>
        ) : null}
        <span className="muted"> · declarado en {link.declared_by === "source" ? left : right}</span>
      </summary>
      {link.type !== "reference" && link.type !== "hypervisor" ? (
        <div className="table-wrap">
          <table className="link-sides">
            <thead>
              <tr>
                <th>Campo</th>
                <th>{left}</th>
                <th>{right}</th>
              </tr>
            </thead>
            <tbody>
              {sideRows(link).map((row) => (
                <tr key={row.label}>
                  <th scope="row">{row.label}</th>
                  <td className={row.source === DASH ? "muted" : "mono"}>{row.source}</td>
                  <td className={row.target === DASH ? "muted" : "mono"}>{row.target}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {link.type === "hypervisor" ? (
        <dl className="facts">
          {Object.entries(link.source.extra).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="mono">{dashValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {attributes.length ? (
        <dl className="facts">
          {attributes.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="mono">{dashValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {link.vlans.length ? (
        <div className="table-wrap">
          <table className="link-vlans">
            <thead>
              <tr>
                <th>VLAN</th>
                <th>Red</th>
                <th>Gateway</th>
                <th>VRF</th>
                <th>Interfaz · IP</th>
              </tr>
            </thead>
            <tbody>
              {link.vlans.map((vlan) => (
                <tr key={String(vlan.id)} title={vlan.description ?? undefined}>
                  <td className="mono">
                    {vlanLabel(vlan)}
                    {vlanNote(vlan) ? <span className="muted"> ({vlanNote(vlan)})</span> : null}
                  </td>
                  <td className="mono">{dashValue(vlan.cidr)}</td>
                  <td className="mono">{dashValue(vlan.gateway)}</td>
                  <td className="mono">{dashValue(vlan.vrf)}</td>
                  <td className="mono">{vlan.interface || vlan.ip ? `${vlan.interface ?? DASH} · ${vlan.ip ?? DASH}` : DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <ul className="link-sources">
        {link.sources.map((source) => (
          <li key={`${source.path}:${source.key}`}>
            <details>
              <summary className="mono">
                {source.path ?? DASH} <span className="muted">→</span> {source.key}
              </summary>
              <pre className="link-yaml">{source.yaml}</pre>
            </details>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function LinkDetailView({ edge, response, fallback, loading, error, findings, compare = false, onOpenEndpoint, onClose }: Props) {
  const detail = response?.detail ?? fallback;
  const diff = response?.diff ?? null;
  const status: DiffStatus | string | undefined = diff?.status ?? edge.diff_status;
  return (
    <div className="link-detail" id="link-detail">
      <header className="link-detail-head">
        <h3>Conexión</h3>
        {onClose ? (
          <button type="button" className="text-link" onClick={onClose} title="Esc">
            Cerrar
          </button>
        ) : null}
      </header>
      {detail ? (
        <div className="link-endpoints">
          <EndpointLink endpoint={detail.source} onOpen={onOpenEndpoint} />
          <span className="link-arrow" aria-hidden="true">
            ⇄
          </span>
          <EndpointLink endpoint={detail.target} onOpen={onOpenEndpoint} />
        </div>
      ) : null}
      {compare && status ? (
        <p className="link-status">
          Estado en el diff: <span className={`value-tag diff-${status}`}>{STATUS_LABEL[status] ?? status}</span>
        </p>
      ) : null}
      {loading ? <p className="muted">Cargando conexión…</p> : null}
      {error && !response ? (
        <p className="muted">
          {fallback ? "El inventario no declara esta conexión; se muestra lo que trae el grafo." : error}
        </p>
      ) : null}
      {detail ? (
        <>
          <dl className="facts link-summary">
            <div>
              <dt>Enlaces lógicos</dt>
              <dd>{detail.links.length || DASH}</dd>
            </div>
            <div>
              <dt>VLANs</dt>
              <dd className="mono">{detail.vlans.length ? detail.vlans.map((vlan) => vlanLabel(vlan)).join(", ") : DASH}</dd>
            </div>
            <div>
              <dt>Puertos</dt>
              <dd className="mono">{detail.ports.length ? detail.ports.join(", ") : DASH}</dd>
            </div>
          </dl>
          {compare && diff && diff.status === "modified" && diff.changes.length ? (
            <section className="link-diff">
              <h4>Cambios entre refs</h4>
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
                    {changeRows(diff.changes).map((row) => (
                      <tr key={row.path} className={`diff-row ${row.status === "modified" ? "overridden" : row.status === "added" ? "added" : "inherited"}`}>
                        <td className="mono">{row.path}</td>
                        <td className="mono">{row.before}</td>
                        <td className="mono">{row.after}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          {findings.length ? (
            <section className="link-findings">
              <h4>Hallazgos relacionados</h4>
              <ul className="findings-list compact">
                {findings.map((finding) => (
                  <li key={`${finding.id}:${finding.entity_id}:${finding.message}`} className={`sev-${finding.severity}`}>
                    <span className={`sev-pill sev-${finding.severity}`}>{finding.severity}</span> <strong>{finding.id}</strong>{" "}
                    {finding.message}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {detail.links.map((link, index) => (
            <LinkCard key={`${link.type}:${link.label}:${link.declared_by}:${index}`} link={link} detail={detail} index={index} />
          ))}
          {detail.paths.length ? (
            <section className="link-paths">
              <h4>Archivos YAML</h4>
              <ul>
                {detail.paths.map((path) => (
                  <li key={path} className="mono">
                    {path}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";

import { GLOBAL_GROUP, groupFindingsBySite, templateName } from "../findings";
import type { Finding, FindingCounts, FindingsPayload } from "../types";

type Props = {
  payload: FindingsPayload | null;
  /** "all" = vista global (agrupada por sitio); si no, el id del sitio. */
  scope: string;
  /** Etiqueta legible del sitio (id → nombre). */
  siteNames?: Record<string, string>;
  /** Ids de sitio conocidos, para listar también sitios sin hallazgos. */
  siteIds?: string[];
  added?: Finding[];
  resolved?: Finding[];
  compare?: boolean;
  open: boolean;
  onClose: () => void;
  onSelect: (finding: Finding) => void;
  onOpenSite?: (site: string) => void;
};

const SEV: Array<Finding["severity"]> = ["error", "warning", "info"];

export function SeverityPills({ counts, zero = true }: { counts: Pick<FindingCounts, "error" | "warning" | "info">; zero?: boolean }) {
  const total = counts.error + counts.warning + counts.info;
  return (
    <>
      {counts.error ? <span className="sev-pill sev-error">{counts.error}</span> : null}
      {counts.warning ? <span className="sev-pill sev-warning">{counts.warning}</span> : null}
      {counts.info ? <span className="sev-pill sev-info">{counts.info}</span> : null}
      {!total && zero ? <span className="sev-pill sev-ok">0</span> : null}
    </>
  );
}

export function FindingsBell({
  counts,
  onClick,
  active,
  scopeLabel,
}: {
  counts: { error: number; warning: number; info: number };
  onClick: () => void;
  active?: boolean;
  /** Sitio al que pertenecen los contadores (vacío en la vista global). */
  scopeLabel?: string | null;
}) {
  return (
    <button
      type="button"
      id="findings-bell"
      className={`findings-bell${active ? " is-active" : ""}`}
      onClick={onClick}
      title={scopeLabel ? `Hallazgos de ${scopeLabel}` : "Hallazgos de todos los sitios"}
    >
      <span className="bell-icon" aria-hidden>
        ⌁
      </span>
      <span className="bell-label">Hallazgos</span>
      <span className="bell-scope">{scopeLabel ?? "global"}</span>
      <SeverityPills counts={counts} />
    </button>
  );
}

function FindingCard({ item, showSite, onSelect }: { item: Finding; showSite?: boolean; onSelect: (finding: Finding) => void }) {
  const tpl = templateName(item);
  return (
    <button type="button" className={`finding-card sev-${item.severity}`} onClick={() => onSelect(item)}>
      <span className="finding-head">
        <span className={`sev-pill sev-${item.severity}`}>{item.severity}</span>
        <strong>{item.id}</strong>
        {showSite && item.site ? <span className="finding-site">{item.site}</span> : null}
      </span>
      {tpl ? (
        <span className="finding-origin" title={item.source_template ?? undefined}>
          plantilla <span className="mono">{tpl}</span> · vía clúster <span className="mono">{item.cluster}</span>
        </span>
      ) : (
        <span className="mono">{item.entity_id}</span>
      )}
      <p>{item.message}</p>
      <p className="reco">{item.recommendation}</p>
    </button>
  );
}

export function FindingsPanel({
  payload,
  scope,
  siteNames = {},
  siteIds = [],
  added,
  resolved,
  compare,
  open,
  onClose,
  onSelect,
  onOpenSite,
}: Props) {
  const [severity, setSeverity] = useState("all");
  const [cluster, setCluster] = useState("all");
  const [rule, setRule] = useState("all");
  const global = scope === "all";
  useEffect(() => {
    setCluster("all");
  }, [scope]);
  const findings = payload?.findings ?? [];
  const clusters = useMemo(
    () => [...new Set(findings.map((item) => item.cluster).filter(Boolean))] as string[],
    [findings],
  );
  const rules = useMemo(() => [...new Set(findings.map((item) => item.id))], [findings]);
  const filtered = findings.filter((item) => {
    if (severity !== "all" && item.severity !== severity) return false;
    if (cluster !== "all" && item.cluster !== cluster) return false;
    if (rule !== "all" && item.id !== rule) return false;
    return true;
  });
  const knownSites = useMemo(
    () => [...new Set([...siteIds, ...Object.keys(payload?.by_site ?? {})])],
    [siteIds, payload],
  );
  const groups = useMemo(() => groupFindingsBySite(filtered, knownSites), [filtered, knownSites]);
  const inScope = (items?: Finding[]) => (global ? (items ?? []) : (items ?? []).filter((item) => item.site === scope));
  if (!open) return null;
  const label = (site: string) => siteNames[site] ?? site;
  return (
    <aside className="findings-drawer" id="findings-panel">
      <header>
        <h2>
          Hallazgos <span className="findings-scope">{global ? "· todos los sitios" : `· ${label(scope)}`}</span>
        </h2>
        <button type="button" className="text-link" onClick={onClose}>
          Cerrar
        </button>
      </header>
      <div className="findings-filters">
        <label>
          Severidad
          <select value={severity} onChange={(event) => setSeverity(event.target.value)} id="findings-filter-severity">
            <option value="all">todas</option>
            {SEV.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Clúster
          <select value={cluster} onChange={(event) => setCluster(event.target.value)} id="findings-filter-cluster">
            <option value="all">todos</option>
            {clusters.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Regla
          <select value={rule} onChange={(event) => setRule(event.target.value)} id="findings-filter-rule">
            <option value="all">todas</option>
            {rules.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      </div>
      {compare ? (
        <p className="muted">
          Comparar: +{inScope(added).length} nuevos · −{inScope(resolved).length} resueltos
        </p>
      ) : null}
      {global ? (
        <div className="findings-groups" id="findings-groups">
          {groups.map((group) => (
            <section key={group.key} className="findings-group" data-site={group.site ?? "global"}>
              <button
                type="button"
                className="findings-group-head"
                disabled={!group.site}
                onClick={() => group.site && onOpenSite?.(group.site)}
                title={group.site ? `Ir a ${label(group.site)}` : "Templates globales sin clúster que los use"}
              >
                <strong>{group.key === GLOBAL_GROUP ? "Global / plantillas" : label(group.site ?? "")}</strong>
                <span className="findings-group-pills">
                  <SeverityPills counts={group.counts} />
                </span>
                {group.site ? <span className="findings-group-go">ver sitio →</span> : null}
              </button>
              {group.findings.length ? (
                <ul className="findings-list">
                  {group.findings.map((item, index) => (
                    <li key={`${item.id}:${item.entity_id}:${item.cluster ?? ""}:${index}`}>
                      <FindingCard item={item} onSelect={onSelect} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ))}
        </div>
      ) : (
        <ul className="findings-list">
          {filtered.map((item, index) => (
            <li key={`${item.id}:${item.entity_id}:${item.cluster ?? ""}:${index}`}>
              <FindingCard item={item} onSelect={onSelect} />
            </li>
          ))}
          {!filtered.length ? <li className="muted">Sin hallazgos en {label(scope)}.</li> : null}
        </ul>
      )}
    </aside>
  );
}

import { useMemo, useState } from "react";

import type { Finding, FindingsPayload } from "../types";

type Props = {
  payload: FindingsPayload | null;
  added?: Finding[];
  resolved?: Finding[];
  compare?: boolean;
  open: boolean;
  onClose: () => void;
  onSelect: (finding: Finding) => void;
};

const SEV: Array<Finding["severity"]> = ["error", "warning", "info"];

export function FindingsBell({
  counts,
  onClick,
  active,
}: {
  counts: { error: number; warning: number; info: number };
  onClick: () => void;
  active?: boolean;
}) {
  const total = counts.error + counts.warning + counts.info;
  return (
    <button
      type="button"
      id="findings-bell"
      className={`findings-bell${active ? " is-active" : ""}`}
      onClick={onClick}
      title="Hallazgos"
    >
      <span className="bell-icon" aria-hidden>
        ⌁
      </span>
      <span className="bell-label">Hallazgos</span>
      {counts.error ? <span className="sev-pill sev-error">{counts.error}</span> : null}
      {counts.warning ? <span className="sev-pill sev-warning">{counts.warning}</span> : null}
      {counts.info ? <span className="sev-pill sev-info">{counts.info}</span> : null}
      {!total ? <span className="sev-pill sev-ok">0</span> : null}
    </button>
  );
}

export function FindingsPanel({ payload, added, resolved, compare, open, onClose, onSelect }: Props) {
  const [severity, setSeverity] = useState("all");
  const [site, setSite] = useState("all");
  const [cluster, setCluster] = useState("all");
  const [rule, setRule] = useState("all");
  const findings = payload?.findings ?? [];
  const sites = useMemo(() => [...new Set(findings.map((item) => item.site).filter(Boolean))] as string[], [findings]);
  const clusters = useMemo(
    () => [...new Set(findings.map((item) => item.cluster).filter(Boolean))] as string[],
    [findings],
  );
  const rules = useMemo(() => [...new Set(findings.map((item) => item.id))], [findings]);
  const filtered = findings.filter((item) => {
    if (severity !== "all" && item.severity !== severity) return false;
    if (site !== "all" && item.site !== site) return false;
    if (cluster !== "all" && item.cluster !== cluster) return false;
    if (rule !== "all" && item.id !== rule) return false;
    return true;
  });
  if (!open) return null;
  return (
    <aside className="findings-drawer" id="findings-panel">
      <header>
        <h2>Hallazgos</h2>
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
          Sitio
          <select value={site} onChange={(event) => setSite(event.target.value)} id="findings-filter-site">
            <option value="all">todos</option>
            {sites.map((item) => (
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
          Comparar: +{added?.length ?? 0} nuevos · −{resolved?.length ?? 0} resueltos
        </p>
      ) : null}
      <ul className="findings-list">
        {filtered.map((item, index) => (
          <li key={`${item.id}:${item.entity_id}:${index}`}>
            <button type="button" className={`finding-card sev-${item.severity}`} onClick={() => onSelect(item)}>
              <span className={`sev-pill sev-${item.severity}`}>{item.severity}</span>
              <strong>{item.id}</strong>
              <span className="mono">{item.entity_id}</span>
              <p>{item.message}</p>
              <p className="reco">{item.recommendation}</p>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

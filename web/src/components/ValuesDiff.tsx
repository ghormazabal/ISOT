import { useMemo, useState } from "react";

import type { ValuesDiff, ValuesDiffEntry, ValuesDiffRelease } from "../types";
import { formatValue } from "./ValuesTree";

type Props = {
  diff: ValuesDiff;
  onOpenTemplate: (site: string, name: string) => void;
  onOpenCluster: (site: string, name: string) => void;
};

const STATUS_LABEL: Record<ValuesDiffEntry["status"], string> = {
  inherited: "heredado",
  overridden: "sobrescrito",
  added: "efectivo (añadido)",
};

export function ValuesDiffView({ diff, onOpenTemplate, onOpenCluster }: Props) {
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const needle = query.trim().toLowerCase();

  const releases = useMemo(
    () =>
      diff.releases.map((release) => ({
        ...release,
        entries: release.entries.filter((entry) => {
          if (onlyChanged && entry.status === "inherited") return false;
          if (!needle) return true;
          return entry.path.toLowerCase().includes(needle);
        }),
      })),
    [diff.releases, needle, onlyChanged],
  );

  const counts = useMemo(() => {
    const all = diff.releases.flatMap((item) => item.entries);
    return {
      inherited: all.filter((item) => item.status === "inherited").length,
      overridden: all.filter((item) => item.status === "overridden").length,
      added: all.filter((item) => item.status === "added").length,
    };
  }, [diff.releases]);

  return (
    <section className="explorer-pane diff-pane">
      <header className="graph-toolbar">
        <div>
          <h2>Values: clúster vs template</h2>
          <p>
            <button type="button" className="text-link" onClick={() => onOpenCluster(diff.cluster_site, diff.cluster)}>
              {diff.cluster_site}/{diff.cluster}
            </button>
            {" · template "}
            <button
              type="button"
              className="text-link"
              onClick={() => onOpenTemplate(diff.template.site, diff.template.name)}
            >
              {diff.template.name}
            </button>
          </p>
        </div>
        <label className="search path-search">
          <span>Ruta</span>
          <input
            id="diff-path-search"
            name="diff-path-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="p.ej. cniConfig.podCIDR"
            autoComplete="off"
          />
        </label>
      </header>
      <ul className="diff-legend">
        <li>
          <i className="swatch inherited" /> heredado ({counts.inherited})
        </li>
        <li>
          <i className="swatch overridden" /> sobrescrito ({counts.overridden})
        </li>
        <li>
          <i className="swatch added" /> efectivo / añadido ({counts.added})
        </li>
        <li>
          <label className="diff-filter">
            <input
              type="checkbox"
              checked={onlyChanged}
              onChange={(event) => setOnlyChanged(event.target.checked)}
            />{" "}
            solo cambios
          </label>
        </li>
      </ul>
      {diff.unknown_releases.length > 0 ? (
        <p className="error">Overrides a releases desconocidos: {diff.unknown_releases.join(", ")}</p>
      ) : null}
      <div className="diff-body">
        {releases.map((release) => (
          <ReleaseBlock key={release.release} release={release} />
        ))}
      </div>
    </section>
  );
}

function ReleaseBlock({ release }: { release: ValuesDiffRelease }) {
  const [open, setOpen] = useState(release.has_override || Boolean(release.error));
  return (
    <article className="diff-release">
      <button type="button" className="diff-release-head" onClick={() => setOpen((value) => !value)}>
        <span>
          {open ? "▾" : "▸"} {release.release}
        </span>
        <span className="muted">
          {release.chart} · {release.version || "versión faltante"} · {release.values_source}
          {release.has_override ? " · con override" : " · sin override"}
        </span>
      </button>
      {open ? (
        release.error ? (
          <p className="error">{release.error}</p>
        ) : release.entries.length === 0 && !release.has_override && release.values_source === "missing" ? (
          <p className="missing-flag">values faltantes en el template; el clúster no declara override</p>
        ) : release.entries.length === 0 ? (
          <p className="muted">sin claves para el filtro actual</p>
        ) : (
          <div className="table-wrap">
            <table className="diff-table">
              <thead>
                <tr>
                  <th>Ruta</th>
                  <th>Template</th>
                  <th>Override</th>
                  <th>Efectivo</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {release.entries.map((entry) => (
                  <tr key={entry.path} className={`diff-row ${entry.status}`}>
                    <td className="mono">{entry.path}</td>
                    <td className="mono">{entry.status === "added" ? "—" : formatValue(entry.template)}</td>
                    <td className="mono">{entry.status === "inherited" ? "—" : formatValue(entry.override)}</td>
                    <td className="mono effective-cell">{formatValue(entry.effective)}</td>
                    <td>{STATUS_LABEL[entry.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </article>
  );
}

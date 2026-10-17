import { useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import type { DiffChange, DiffEntity, SemanticDiff } from "../types";
import { formatValue, ValuesTree, valuesToTree } from "./ValuesTree";
import { Splitter, usePanelSize } from "./Splitter";
import { COMPARE_SIDE_SPEC } from "../resize";

type Props = {
  diff: SemanticDiff;
  onSelectEntity: (entity: DiffEntity) => void;
};

const KIND_LABEL: Record<DiffChange["status"], string> = {
  added: "agregado",
  removed: "quitado",
  modified: "modificado",
};

export function CompareValues({ diff, onSelectEntity }: Props) {
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const split = usePanelSize("compare.side-split", COMPARE_SIDE_SPEC);
  const [sideBySide, setSideBySide] = useState(false);
  const needle = query.trim().toLowerCase();

  const groups = useMemo(() => {
    return diff.entities
      .filter((item) => item.kind === "values")
      .filter((item) => (onlyChanged ? item.status !== "unchanged" : true))
      .map((item) => ({
        entity: item,
        changes: item.changes.filter((row) => !needle || row.path.toLowerCase().includes(needle)),
      }))
      .filter((item) => item.changes.length > 0 || (!needle && item.entity.status !== "unchanged"));
  }, [diff.entities, needle, onlyChanged]);

  return (
    <section className="explorer-pane diff-pane">
      <header className="graph-toolbar">
        <div>
          <h2>Values entre refs</h2>
          <p className="mono">
            {diff.base.ref.slice(0, 12)} → {diff.head.ref.slice(0, 12)}
          </p>
        </div>
        <label className="search path-search">
          <span>Ruta</span>
          <input
            id="compare-path-search"
            name="compare-path-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="p.ej. cniConfig.mtu"
            autoComplete="off"
          />
        </label>
      </header>
      <ul className="diff-legend">
        <li>
          <i className="swatch added" /> agregado
        </li>
        <li>
          <i className="swatch overridden" /> modificado
        </li>
        <li>
          <i className="swatch missing" /> quitado
        </li>
        <li>
          <label className="diff-filter">
            <input type="checkbox" checked={onlyChanged} onChange={(event) => setOnlyChanged(event.target.checked)} />{" "}
            colapsar sin cambios
          </label>
        </li>
        <li>
          <label className="diff-filter">
            <input type="checkbox" checked={sideBySide} onChange={(event) => setSideBySide(event.target.checked)} /> lado
            a lado
          </label>
        </li>
      </ul>
      <div className="diff-body">
        {groups.length === 0 ? <p className="muted">Sin cambios de values para el filtro actual.</p> : null}
        {groups.map(({ entity, changes }) => (
          <article key={entity.id} className="diff-release">
            <button type="button" className="diff-release-head" onClick={() => onSelectEntity(entity)}>
              <span>
                {entity.label} <span className={`value-tag ${statusTag(entity.status)}`}>{entity.status}</span>
              </span>
              <span className="muted">{entity.site || "global"}</span>
            </button>
            {sideBySide ? (
              <SideBySide
                split={split.size}
                onSplit={split.setSize}
                onReset={split.reset}
                left={
                  <>
                    <h3>Base</h3>
                    <ValuesTree nodes={valuesToTree(entity.before)} query={query} emptyLabel="—" defaultOpen />
                  </>
                }
                right={
                  <>
                    <h3>Head</h3>
                    <ValuesTree nodes={valuesToTree(entity.after)} query={query} emptyLabel="—" defaultOpen />
                  </>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="diff-table">
                  <thead>
                    <tr>
                      <th>Ruta</th>
                      <th>Antes</th>
                      <th>Después</th>
                      <th>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changes.map((row) => (
                      <tr key={row.path} className={`diff-row ${row.status === "modified" ? "overridden" : row.status}`}>
                        <td className="mono">{row.path}</td>
                        <td className="mono">{row.status === "added" ? "—" : formatValue(row.before)}</td>
                        <td className="mono">{row.status === "removed" ? "—" : formatValue(row.after)}</td>
                        <td>{KIND_LABEL[row.status]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function statusTag(status: DiffEntity["status"]): string {
  if (status === "added") return "added";
  if (status === "removed") return "missing";
  if (status === "modified") return "overridden";
  return "inherited";
}

/** Base y Head lado a lado con divisor; la proporción (%) es la misma para todos los bloques. */
function SideBySide({
  split,
  onSplit,
  onReset,
  left,
  right,
}: {
  split: number;
  onSplit: (value: number) => void;
  onReset: () => void;
  left: ReactNode;
  right: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  return (
    <div className="compare-side" ref={ref} style={{ "--side-split": `${split}%` } as CSSProperties}>
      <div>{left}</div>
      <Splitter
        className="compare-side-split"
        orientation="vertical"
        label="Proporción Base / Head"
        size={split}
        spec={COMPARE_SIDE_SPEC}
        sign={1}
        onSize={onSplit}
        onReset={onReset}
        containerRef={ref}
      />
      <div>{right}</div>
    </div>
  );
}

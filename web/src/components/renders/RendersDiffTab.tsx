import { useEffect, useMemo, useState } from "react";

import { driftLabel } from "../../renders";
import { rendersApi } from "../../rendersApi";
import type { DiffFile, DiffInstance, RendersDiff } from "../../rendersTypes";
import { ErrorList, errorMessage, FileTree, SideBySideDiff, StatusBadge, UnifiedDiff, WriteDialog, type WriteFlow, type WriteMode } from "./common";

type Props = {
  refName: string | null;
  version: number;
  branches: string[];
  write: WriteMode;
  instance: string | null;
  onInstance: (id: string | null) => void;
  onAfterCommit: () => void;
};

type FileRef = DiffFile & { instance: string };

const STATUS_ORDER = ["new", "modified", "orphaned", "unchanged"] as const;

/** Render fresco vs carpeta destino: árbol con estados + diff unificado o lado a lado + aplicar. */
export function RendersDiffTab({ refName, version, branches, write, instance, onInstance, onAfterCommit }: Props) {
  const [target, setTarget] = useState("local");
  const [targetRef, setTargetRef] = useState("");
  const [data, setData] = useState<RendersDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const [hideUnchanged, setHideUnchanged] = useState(true);
  const [prune, setPrune] = useState(false);
  const [flow, setFlow] = useState<WriteFlow | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    rendersApi
      .diff({ target, target_ref: targetRef || null, ref: refName })
      .then((value) => {
        if (cancelled) return;
        setData(value);
      })
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [target, targetRef, refName, version, reload]);

  const instances: DiffInstance[] = useMemo(() => (data?.instances ?? []).filter((item) => !instance || item.id === instance), [data, instance]);
  const files: FileRef[] = useMemo(
    () =>
      instances.flatMap((item) =>
        item.files
          .filter((file) => !hideUnchanged || file.status !== "unchanged")
          .map((file) => ({ ...file, instance: item.id, path: file.target_path })),
      ),
    [instances, hideUnchanged],
  );
  const counts = useMemo(() => {
    const total = { new: 0, modified: 0, unchanged: 0, orphaned: 0 };
    for (const item of instances) for (const key of STATUS_ORDER) total[key] += item.counts[key];
    return total;
  }, [instances]);

  useEffect(() => {
    if (!files.some((file) => file.path === selected)) setSelected(files[0]?.path ?? null);
  }, [files, selected]);

  const current = files.find((file) => file.path === selected) ?? null;
  const currentTarget = data?.targets.find((item) => item.id === target) ?? data?.target ?? null;
  const drifting = instances.filter((item) => !item.in_sync).map((item) => item.id);

  const applyBlocked = !write.canWrite
    ? write.reason
    : refName
      ? "Aplicar usa el working tree del repo de datos: vuelve a «working tree» para aplicar."
      : targetRef
        ? "Estás comparando contra una rama del destino; aplica contra el working tree (deja la rama vacía)."
        : currentTarget?.error
          ? currentTarget.error
          : !drifting.length
            ? "El destino ya está al día."
            : null;

  const apply = () => {
    const body = { target, instances: instance ? [instance] : undefined, prune };
    setFlow({
      title: `Aplicar renders en «${currentTarget?.name ?? target}»`,
      description: `Escribe ${instance ? `la instancia ${instance}` : "todas las instancias con drift"} en ${currentTarget?.dir ?? "rendered"}/ de una rama wizard/… nueva${currentTarget?.external ? " del repo destino" : ""}${prune ? " y borra los huérfanos" : ""}. Cada carpeta lleva su .isot-render.json.`,
      preview: () => rendersApi.applyPreview(body),
      commit: () => rendersApi.applyCommit(body),
    });
  };

  return (
    <div className="renders-body renders-diff">
      <div className="render-diff-controls">
        <label className="site-picker">
          <span>Destino</span>
          <select value={target} onChange={(event) => setTarget(event.target.value)}>
            {(data?.targets ?? [{ id: "local", name: "local", dir: "rendered", external: false, repo: null, error: null }]).map((item) => (
              <option key={item.id} value={item.id}>
                {item.id} · {item.name}/{item.dir}
              </option>
            ))}
          </select>
        </label>
        <label className="site-picker">
          <span>Rama del destino</span>
          <input list="render-target-refs" value={targetRef} placeholder="working tree" onChange={(event) => setTargetRef(event.target.value.trim())} />
          <datalist id="render-target-refs">{currentTarget?.external ? null : branches.map((name) => <option key={name} value={name} />)}</datalist>
        </label>
        <label className="site-picker">
          <span>Instancia</span>
          <select value={instance ?? ""} onChange={(event) => onInstance(event.target.value || null)}>
            <option value="">todas</option>
            {(data?.instances ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={hideUnchanged} onChange={(event) => setHideUnchanged(event.target.checked)} />
          <span>ocultar iguales</span>
        </label>
        <nav className="view-tabs">
          <button type="button" className={mode === "unified" ? "is-active" : ""} onClick={() => setMode("unified")}>
            Unificado
          </button>
          <button type="button" className={mode === "split" ? "is-active" : ""} onClick={() => setMode("split")}>
            Lado a lado
          </button>
        </nav>
        <button type="button" className="link-button" onClick={() => setReload((value) => value + 1)}>
          ↻ recalcular
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {loading && !data ? <p className="muted">Renderizando y comparando…</p> : null}
      {data ? (
        <>
          <div className="render-diff-summary">
            {STATUS_ORDER.map((key) => (
              <span key={key} className={`render-status is-${key}`}>
                {counts[key]} {key === "new" ? "nuevos" : key === "modified" ? "modificados" : key === "orphaned" ? "huérfanos" : "iguales"}
              </span>
            ))}
            <span className="muted">
              {instances.filter((item) => item.in_sync).length} instancia(s) en sync · {drifting.length} con drift
              {targetRef ? ` · destino en ${targetRef}` : " · destino: working tree"}
            </span>
          </div>
          <div className="render-diff-layout">
            <aside className="render-diff-tree">
              {instances.map((item) => (
                <div key={item.id} className="render-diff-instance">
                  <button type="button" className="render-diff-instance-head" onClick={() => onInstance(instance === item.id ? null : item.id)}>
                    <strong>{item.id}</strong>
                    <span className={item.in_sync ? "render-status is-unchanged" : "render-status is-modified"}>{item.in_sync ? "en sync" : driftLabel(item.counts)}</span>
                    {item.stale ? <span className="render-status is-orphaned" title="El .isot-render.json del destino es de otra versión del template">versión vieja</span> : null}
                  </button>
                  <div className="muted render-diff-dir">
                    <code>{item.dir}/</code> · {item.template}@{item.version}
                  </div>
                  {item.target_error ? <p className="error">{item.target_error}</p> : null}
                  <ErrorList errors={item.errors} warnings={item.warnings} />
                  {item.skipped.length ? <p className="muted">Ignorados (no regulares o fuera del destino): {item.skipped.join(", ")}</p> : null}
                </div>
              ))}
              <h4>Archivos</h4>
              <FileTree items={files} selected={selected} onSelect={(file) => setSelected(file.path)} badge={(file) => <StatusBadge status={file.status} />} />
            </aside>
            <section className="render-diff-view">
              {current ? (
                <>
                  <header>
                    <StatusBadge status={current.status} /> <code>{current.path}</code> <span className="muted">({current.instance})</span>
                  </header>
                  {current.status === "orphaned" ? (
                    <p className="muted">Existe en el destino pero el render ya no lo genera. «Borrar huérfanos» al aplicar lo elimina.</p>
                  ) : null}
                  {mode === "unified" ? <UnifiedDiff diff={current.diff} /> : <SideBySideDiff before={current.before} after={current.after} />}
                </>
              ) : (
                <p className="muted">{files.length ? "Elige un archivo." : "Nada que mostrar: el destino coincide con el render."}</p>
              )}
            </section>
          </div>
          <div className="render-apply">
            <label className="checkbox">
              <input type="checkbox" checked={prune} onChange={(event) => setPrune(event.target.checked)} disabled={Boolean(applyBlocked)} />
              <span>borrar huérfanos</span>
            </label>
            <div className="wizard-actions">
              <button type="button" className="primary" disabled={Boolean(applyBlocked)} title={applyBlocked ?? ""} onClick={apply}>
                Aplicar {instance ? instance : "todo"}…
              </button>
            </div>
            {applyBlocked ? <span className="muted">{applyBlocked}</span> : <span className="muted">Se muestra el diff completo antes de confirmar. Nunca escribe en main.</span>}
          </div>
        </>
      ) : null}
      {flow ? (
        <WriteDialog
          flow={flow}
          onClose={() => setFlow(null)}
          onDone={(result) => {
            if (!currentTarget?.external) setTargetRef(result.branch);
            onAfterCommit();
          }}
        />
      ) : null}
    </div>
  );
}

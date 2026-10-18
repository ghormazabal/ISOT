import { useEffect, useMemo, useState } from "react";

import { isLocalRef, refLabel, type CompareRefs } from "../../compare";
import { driftLabel } from "../../renders";
import { rendersApi } from "../../rendersApi";
import type { DiffFile, DiffInstance, RendersCompare, RendersDiff } from "../../rendersTypes";
import { FilesCompare } from "../FilesCompare";
import { ErrorList, errorMessage, FileList, SideBySideDiff, StatusBadge, UnifiedDiff, useWrap, ViewerBar, WriteDialog, type WriteFlow, type WriteMode } from "./common";

type Props = {
  refName: string | null;
  /** Comparar global (barra superior): head = datos que se renderizan; base = destino / salida contra la que se compara. */
  compare: CompareRefs | null;
  version: number;
  write: WriteMode;
  instance: string | null;
  onInstance: (id: string | null) => void;
  onAfterCommit: () => void;
};

type FileRef = DiffFile & { instance: string };

const STATUS_ORDER = ["new", "modified", "orphaned", "unchanged"] as const;

type DiffView = "drift" | "output" | "files";

/**
 * Render fresco vs carpeta destino: árbol con estados + diff unificado o lado a lado + aplicar.
 * Con Comparar activo: destino en base vs render en head, salida base→head, o templates/renders.yaml.
 */
export function RendersDiffTab({ refName, compare, version, write, instance, onInstance, onAfterCommit }: Props) {
  const [view, setView] = useState<DiffView>("drift");
  const [wrap, setWrap] = useWrap();
  const targetRef = compare && !isLocalRef(compare.base) ? compare.base : "";
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
      .diff({ target_ref: targetRef || null, ref: refName })
      .then((value) => {
        if (cancelled) return;
        setData(value);
      })
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [targetRef, refName, version, reload]);

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
  const drifting = instances.filter((item) => !item.in_sync).map((item) => item.id);

  const applyBlocked = !write.canWrite
    ? write.reason
    : refName
      ? "Aplicar usa el working tree del repo de datos: pon head = Local para aplicar."
      : targetRef
        ? `El destino se lee en ${refLabel(targetRef)}: pon base = Local (o sal de Comparar) para aplicar.`
        : data?.error
          ? data.error
          : !drifting.length
            ? "El destino ya está al día."
            : null;

  const apply = () => {
    const body = { instances: instance ? [instance] : undefined, prune };
    setFlow({
      title: "Aplicar renders en este repo de datos",
      description: `Escribe ${instance ? `la instancia ${instance}` : "todas las instancias con drift"} en su carpeta destino (${data?.target_dir ?? "rendered"}/ o la propia de la instancia) del working tree de este mismo repo, sin commit${prune ? ", y borra los huérfanos" : ""}. Cada carpeta lleva su .isot-render.json.`,
      preview: () => rendersApi.applyPreview(body),
      write: (token) => rendersApi.applyWrite({ ...body, token }),
    });
  };

  const modeTabs = compare ? (
    <nav className="view-tabs render-compare-modes" aria-label="Qué comparar">
      <button type="button" id="render-view-drift" className={view === "drift" ? "is-active" : ""} onClick={() => setView("drift")} title="Carpeta destino leída en base vs render fresco de head">
        Destino (base) vs render (head)
      </button>
      <button type="button" id="render-view-output" className={view === "output" ? "is-active" : ""} onClick={() => setView("output")} title="Lo que renderiza base vs lo que renderiza head">
        Salida base → head
      </button>
      <button type="button" id="render-view-files" className={view === "files" ? "is-active" : ""} onClick={() => setView("files")} title="manifest-templates/ y renders.yaml en base vs head">
        Templates y renders.yaml
      </button>
    </nav>
  ) : null;

  if (compare && view === "output") {
    return (
      <div className="renders-body renders-diff">
        <div className="render-diff-controls">{modeTabs}</div>
        <OutputCompare refs={compare} instance={instance} onInstance={onInstance} version={version} />
      </div>
    );
  }
  if (compare && view === "files") {
    return (
      <div className="renders-body renders-diff">
        <div className="render-diff-controls">{modeTabs}</div>
        <FilesCompare refs={compare} prefix={["manifest-templates", "renders.yaml"]} title="Templates y renders.yaml" className="render-files-compare" />
      </div>
    );
  }

  return (
    <div className="renders-body renders-diff">
      <div className="render-diff-controls">
        {modeTabs}
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
      {data?.error ? <p className="error">{data.error}</p> : null}
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
              {` · carpeta destino ${data.target_dir}/`}
              {targetRef ? ` · destino leído en ${refLabel(targetRef)}` : " · destino en working tree"}
              {refName ? ` · render de ${refLabel(refName)}` : ""}
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
              <FileList items={files} selected={selected} onSelect={(file) => setSelected(file.path)} badge={(file) => <StatusBadge status={file.status} />} />
            </aside>
            <section className={`render-diff-view${wrap ? " is-wrap" : ""}`}>
              {current ? (
                <>
                  <ViewerBar path={current.path} wrap={wrap} onWrap={setWrap} copy={current.after ?? current.before ?? ""} before={<StatusBadge status={current.status} />}>
                    <span className="muted">{current.instance}</span>
                  </ViewerBar>
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
          onDone={() => {
            // Guardado en el working tree: el Diff (Local) ya debería verse en sync.
            onAfterCommit();
          }}
        />
      ) : null}
    </div>
  );
}

/** Lo que renderiza base vs lo que renderiza head (por ruta final de salida). */
function OutputCompare({ refs, instance, onInstance, version }: { refs: CompareRefs; instance: string | null; onInstance: (id: string | null) => void; version: number }) {
  const [data, setData] = useState<RendersCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hideUnchanged, setHideUnchanged] = useState(true);
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const [selected, setSelected] = useState<string | null>(null);
  const [wrap, setWrap] = useWrap();

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setData(null);
    rendersApi
      .compare({ base: refs.base, head: refs.head, instance })
      .then((value) => !cancelled && setData(value))
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [refs.base, refs.head, instance, version]);

  const files = useMemo(() => (data?.files ?? []).filter((file) => !hideUnchanged || file.status !== "unchanged"), [data, hideUnchanged]);
  useEffect(() => {
    if (!files.some((file) => file.path === selected)) setSelected(files[0]?.path ?? null);
  }, [files, selected]);
  const current = files.find((file) => file.path === selected) ?? null;

  return (
    <>
      <div className="render-diff-controls">
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
      </div>
      {error ? <p className="error">{error}</p> : null}
      {!data && !error ? <p className="muted">Renderizando base y head…</p> : null}
      {data ? (
        <>
          <div className="render-diff-summary">
            <span className="render-status is-added">{data.counts.added} añadidos</span>
            <span className="render-status is-removed">{data.counts.removed} eliminados</span>
            <span className="render-status is-modified">{data.counts.modified} modificados</span>
            <span className="render-status is-unchanged">{data.counts.unchanged} iguales</span>
            <span className="muted">
              salida de {refLabel(refs.base)} → {refLabel(refs.head)} · {data.templates.files.length} archivo(s) de templates/renders.yaml cambian
            </span>
          </div>
          <div className="render-diff-layout">
            <aside className="render-diff-tree">
              {data.instances.map((item) => (
                <div key={item.id} className="render-diff-instance">
                  <button type="button" className="render-diff-instance-head" onClick={() => onInstance(instance === item.id ? null : item.id)}>
                    <strong>{item.id}</strong> <StatusBadge status={item.status} />
                  </button>
                  <div className="muted render-diff-dir">
                    <code>{item.dir}/</code> · {item.template}@{item.base_version && item.base_version !== item.version ? `${item.base_version}→${item.version}` : item.version}
                  </div>
                  <ErrorList errors={item.errors} warnings={[]} />
                </div>
              ))}
              {[...data.errors.base.map((e) => `base: ${e}`), ...data.errors.head.map((e) => `head: ${e}`)].map((e) => (
                <p key={e} className="error">
                  {e}
                </p>
              ))}
              <h4>Archivos</h4>
              <FileList items={files} selected={selected} onSelect={(file) => setSelected(file.path)} badge={(file) => <StatusBadge status={file.status} />} />
            </aside>
            <section className={`render-diff-view${wrap ? " is-wrap" : ""}`}>
              {current ? (
                <>
                  <ViewerBar path={current.path} wrap={wrap} onWrap={setWrap} copy={current.after ?? current.before ?? ""} before={<StatusBadge status={current.status} />}>
                    <span className="muted">{current.instance}</span>
                  </ViewerBar>
                  {mode === "unified" ? (
                    <UnifiedDiff diff={current.diff} />
                  ) : (
                    <SideBySideDiff before={current.before} after={current.after} leftLabel={`base · ${refLabel(refs.base)}`} rightLabel={`head · ${refLabel(refs.head)}`} />
                  )}
                </>
              ) : (
                <p className="muted">{files.length ? "Elige un archivo." : "La salida es igual en base y head."}</p>
              )}
            </section>
          </div>
        </>
      ) : null}
    </>
  );
}

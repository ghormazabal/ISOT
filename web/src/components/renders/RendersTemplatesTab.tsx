import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { api } from "../../api";
import type { PanelSpec } from "../../resize";
import { appendOutput, getTopLevelKey, newTemplateSkeleton, SAFE_FILE_NAME, SAFE_TEMPLATE_ID, setTopLevelKey } from "../../renders";
import { rendersApi } from "../../rendersApi";
import type { RenderError, RenderInstance, RenderPreview, RenderTemplateSummary } from "../../rendersTypes";
import type { ClusterIndexEntry, SiteIndexEntry } from "../../types";
import { Splitter, usePanelSize } from "../Splitter";
import { ValuesTree, valuesToTree } from "../ValuesTree";
import { CodeEditor, ErrorList, errorMessage, FileTree, WriteDialog, type WriteFlow, type WriteMode } from "./common";

const SPEC_FILE = "template.yaml";
const LIBRARY_SPEC: PanelSpec = { defaultSize: 250, min: 180, max: 420, collapsible: true };
const PREVIEW_SPEC: PanelSpec = { defaultSize: 460, min: 300, max: 900 };

type Doc = {
  id: string;
  /** repo = existe en manifest-templates/; bundled = incluido (solo lectura); draft = nuevo/clon sin guardar. */
  source: "repo" | "bundled" | "draft";
  spec: string;
  files: Record<string, string>;
  saved: string;
  usedBy: RenderInstance[];
  params: Record<string, unknown>;
};

function snapshot(spec: string, files: Record<string, string>): string {
  return JSON.stringify([spec, Object.entries(files).sort(([a], [b]) => a.localeCompare(b))]);
}

type Props = {
  refName: string | null;
  version: number;
  sites: SiteIndexEntry[];
  write: WriteMode;
  focus: { id: string; source?: "repo" | "bundled"; key: number } | null;
  onAfterCommit: () => void;
  onViewBranch: (branch: string) => void;
};

export function RendersTemplatesTab({ refName, version, sites, write, focus, onAfterCommit, onViewBranch }: Props) {
  const library = usePanelSize("renders.library", LIBRARY_SPEC);
  const previewWidth = usePanelSize("renders.preview", PREVIEW_SPEC);

  const [templates, setTemplates] = useState<RenderTemplateSummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [active, setActive] = useState(SPEC_FILE);
  const [jump, setJump] = useState<{ line: number; key: number } | null>(null);
  const [cloneId, setCloneId] = useState("");
  const [newFile, setNewFile] = useState("");
  const [output, setOutput] = useState<{ template: string; mode: "single" | "per_item"; path: string }>({ template: "", mode: "per_item", path: "{{ item.name }}.yaml" });
  const [notice, setNotice] = useState<string | null>(null);
  const [flow, setFlow] = useState<WriteFlow | null>(null);

  const [site, setSite] = useState(() => sites[0]?.id ?? "");
  const [cluster, setCluster] = useState("");
  const [clusters, setClusters] = useState<ClusterIndexEntry[]>([]);
  const [instance, setInstance] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [preview, setPreview] = useState<RenderPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [selectedOut, setSelectedOut] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"salida" | "contexto">("salida");
  const [context, setContext] = useState<Record<string, unknown> | null>(null);
  const [contextQuery, setContextQuery] = useState("");

  useEffect(() => {
    if (!site && sites[0]) setSite(sites[0].id);
  }, [sites, site]);

  const loadList = useCallback(() => {
    rendersApi
      .templates(refName)
      .then((list) => {
        setTemplates(list);
        setListError(null);
      })
      .catch((err: unknown) => setListError(errorMessage(err)));
  }, [refName]);
  useEffect(loadList, [loadList, version]);

  const dirty = doc ? snapshot(doc.spec, doc.files) !== doc.saved : false;

  const open = useCallback(
    async (id: string, source?: "repo" | "bundled") => {
      if (dirty && !window.confirm("Hay cambios sin guardar en el editor. ¿Descartarlos?")) return;
      try {
        const detail = await rendersApi.template(id, source ?? null, refName);
        const kind = detail.source === "repo" ? "repo" : "bundled";
        setDoc({
          id: detail.id,
          source: kind,
          spec: detail.spec_text,
          files: detail.file_contents,
          saved: snapshot(detail.spec_text, detail.file_contents),
          usedBy: detail.used_by,
          params: detail.params,
        });
        setActive(SPEC_FILE);
        setCloneId(kind === "bundled" ? `${detail.id}-custom` : "");
        setNotice(null);
        const first = detail.used_by[0];
        if (first) {
          setInstance(first.id);
          setSite(first.site);
          setCluster(first.cluster ?? "");
        } else {
          setInstance("");
        }
        setParamsText("{}");
      } catch (err) {
        setNotice(errorMessage(err));
      }
    },
    [dirty, refName],
  );

  useEffect(() => {
    if (focus) void open(focus.id, focus.source);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.key]);

  useEffect(() => {
    if (!doc && templates.length && !focus) {
      const first = templates.find((item) => item.source === "repo") ?? templates[0];
      void open(first.id, first.source === "repo" ? "repo" : "bundled");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templates]);

  useEffect(() => {
    if (!site) return;
    api
      .clusters(site)
      .then(setClusters)
      .catch(() => setClusters([]));
  }, [site]);

  // Contexto del render (explorador) para el sitio/cluster elegidos.
  useEffect(() => {
    if (!site) return;
    let cancelled = false;
    rendersApi
      .context(site, cluster || null, refName)
      .then((value) => !cancelled && setContext(value))
      .catch(() => !cancelled && setContext(null));
    return () => {
      cancelled = true;
    };
  }, [site, cluster, refName]);

  // Preview en vivo (debounce) del borrador del editor.
  const seq = useRef(0);
  useEffect(() => {
    if (!doc || !site) return;
    let params: Record<string, unknown> = {};
    try {
      params = paramsText.trim() ? (JSON.parse(paramsText) as Record<string, unknown>) : {};
    } catch {
      setPreviewError("params de la preview: JSON inválido");
      return;
    }
    const current = ++seq.current;
    const timer = window.setTimeout(() => {
      setPreviewing(true);
      rendersApi
        .preview({ id: doc.id, spec_text: doc.spec, files: doc.files, site, cluster: cluster || null, params, instance: instance || undefined, ref: refName })
        .then((value) => {
          if (current !== seq.current) return;
          setPreview(value);
          setPreviewError(null);
          setSelectedOut((prev) => (prev && value.files.some((file) => file.path === prev) ? prev : value.files[0]?.path ?? null));
        })
        .catch((err: unknown) => current === seq.current && setPreviewError(errorMessage(err)))
        .finally(() => current === seq.current && setPreviewing(false));
    }, 450);
    return () => window.clearTimeout(timer);
  }, [doc?.spec, doc?.files, doc?.id, site, cluster, instance, paramsText, refName]);

  const readOnly = !doc || doc.source === "bundled" || !write.canWrite;
  const allErrors: RenderError[] = useMemo(() => [...(preview?.template_errors ?? []), ...(preview?.errors ?? [])], [preview]);
  const errorLines = useMemo(() => {
    const lines = new Set<number>();
    for (const error of allErrors) {
      const file = error.file === "template.yaml" || error.file === null ? SPEC_FILE : error.file;
      if (file === active && error.line) lines.add(error.line);
    }
    return lines;
  }, [allErrors, active]);

  const updateActive = (value: string) => {
    if (!doc) return;
    if (active === SPEC_FILE) setDoc({ ...doc, spec: value });
    else setDoc({ ...doc, files: { ...doc.files, [active]: value } });
  };

  const jumpTo = (file: string, line: number) => {
    const name = file === "template.yaml" ? SPEC_FILE : file;
    if (name !== SPEC_FILE && !(doc && name in doc.files)) return;
    setActive(name);
    setJump({ line, key: Date.now() });
  };

  const createNew = () => {
    if (dirty && !window.confirm("Hay cambios sin guardar en el editor. ¿Descartarlos?")) return;
    let id = "mi-render";
    let n = 1;
    while (templates.some((item) => item.id === id)) id = `mi-render-${++n}`;
    const skeleton = newTemplateSkeleton(id);
    setDoc({ id, source: "draft", spec: skeleton.spec, files: skeleton.files, saved: "", usedBy: [], params: {} });
    setActive(SPEC_FILE);
    setInstance("");
    setNotice("Template nuevo (borrador). Cambia el id en template.yaml y guárdalo en el repo de datos.");
  };

  const cloneToRepo = async () => {
    if (!doc) return;
    if (!SAFE_TEMPLATE_ID.test(cloneId)) {
      setNotice("id inválido: minúsculas, números, . _ - (máx. 63)");
      return;
    }
    try {
      const draft = await rendersApi.clone(doc.id, cloneId);
      setDoc({ id: draft.id, source: "draft", spec: draft.spec_text, files: draft.file_contents, saved: "", usedBy: [], params: doc.params });
      setActive(SPEC_FILE);
      setNotice(
        draft.exists
          ? `Ya existe manifest-templates/${draft.id} en el repo: guardar lo sobrescribe.`
          : `Copia de «${draft.from}» lista para personalizar; se guardará en manifest-templates/${draft.id}/ del repo de datos (no en ISoT).`,
      );
    } catch (err) {
      setNotice(errorMessage(err));
    }
  };

  const specId = doc ? getTopLevelKey(doc.spec, "id") || doc.id : "";

  const save = () => {
    if (!doc) return;
    const body = { id: specId, spec_text: doc.spec, files: doc.files, branch: write.branch };
    setFlow({
      title: `Guardar template ${specId}`,
      description: `Se escribe en manifest-templates/${specId}/ del repo de datos.`,
      preview: () => rendersApi.templateSavePreview(body),
      commit: () => rendersApi.templateSaveCommit(body),
    });
  };

  const remove = () => {
    if (!doc || doc.source !== "repo") return;
    const body = { id: doc.id, delete_template: true, branch: write.branch };
    setFlow({
      title: `Borrar template ${doc.id}`,
      description: doc.usedBy.length ? `Ojo: lo usan ${doc.usedBy.length} instancia(s); quedarán con error REN-001.` : undefined,
      preview: () => rendersApi.templateSavePreview(body),
      commit: () => rendersApi.templateSaveCommit(body),
    });
  };

  const addFile = () => {
    if (!doc) return;
    const name = newFile.trim();
    if (!SAFE_FILE_NAME.test(name) || name === SPEC_FILE || name in doc.files) {
      setNotice("Nombre de archivo inválido o repetido (letras, números, . _ -; p. ej. node.yaml.j2).");
      return;
    }
    setDoc({ ...doc, files: { ...doc.files, [name]: "" } });
    setActive(name);
    setNewFile("");
  };

  const removeFile = () => {
    if (!doc || active === SPEC_FILE) return;
    const files = { ...doc.files };
    delete files[active];
    setDoc({ ...doc, files });
    setActive(SPEC_FILE);
  };

  const repoTemplates = templates.filter((item) => item.source === "repo");
  const bundled = templates.filter((item) => item.source.startsWith("bundled"));
  const selectedFile = preview?.files.find((file) => file.path === selectedOut) ?? null;
  const fileNames = doc ? Object.keys(doc.files).sort() : [];

  const libraryItem = (item: RenderTemplateSummary) => {
    const source = item.source === "repo" ? "repo" : "bundled";
    const isActive = doc?.id === item.id && (doc.source === source || (doc.source === "repo" && source === "repo"));
    return (
      <li key={`${item.source}:${item.id}`}>
        <button type="button" className={`render-lib-item${isActive ? " is-active" : ""}`} onClick={() => void open(item.id, source)}>
          <span className="render-lib-title">
            {item.id} <span className="muted">v{item.version}</span>
          </span>
          <span className="muted">
            {item.scope} · {item.outputs.length} salida(s)
            {item.instances ? ` · ${item.instances} instancia(s)` : ""}
            {item.source === "bundled-overridden" ? " · sobrescrito por el repo" : ""}
          </span>
          {item.description ? <span className="render-lib-desc">{item.description}</span> : null}
          {item.errors.length ? <span className="error">{item.errors.length} error(es)</span> : null}
        </button>
      </li>
    );
  };

  return (
    <div className="renders-templates" style={{ "--lib-w": `${library.size}px`, "--preview-w": `${previewWidth.size}px` } as CSSProperties}>
      <aside className="render-library" hidden={library.size === 0}>
        <div className="wizard-actions">
          <button type="button" className="primary" disabled={!write.canWrite} title={write.reason} onClick={createNew}>
            + Nuevo template
          </button>
        </div>
        {listError ? <p className="error">{listError}</p> : null}
        <h4>Repo de datos</h4>
        {repoTemplates.length ? <ul>{repoTemplates.map(libraryItem)}</ul> : <p className="muted">Ninguno aún: clona uno incluido o crea uno nuevo.</p>}
        <h4>Incluidos en ISoT (solo lectura)</h4>
        <ul>{bundled.map(libraryItem)}</ul>
      </aside>
      <Splitter
        id="split-renders-library"
        orientation="vertical"
        label="Ancho de la biblioteca de templates"
        size={library.size}
        spec={LIBRARY_SPEC}
        sign={1}
        onSize={library.setSize}
        onReset={library.reset}
      />
      <section className="render-editor">
        {doc ? (
          <>
            <header className="render-editor-head">
              <div>
                <h3>
                  {specId} {dirty ? <span className="draft-pill">sin guardar</span> : null}
                </h3>
                <p className="muted">
                  {doc.source === "bundled"
                    ? "Incluido en ISoT · solo lectura"
                    : doc.source === "repo"
                      ? `manifest-templates/${doc.id}/ en el repo de datos`
                      : "Borrador · se guardará en el repo de datos"}
                  {doc.usedBy.length ? ` · usado por ${doc.usedBy.map((item) => item.id).join(", ")}` : ""}
                </p>
              </div>
              <div className="wizard-actions">
                {doc.source === "bundled" ? (
                  <>
                    <input aria-label="id del clon" value={cloneId} onChange={(event) => setCloneId(event.target.value)} placeholder="id en el repo" />
                    <button type="button" className="primary" disabled={!write.canWrite} title={write.reason} onClick={cloneToRepo}>
                      Clonar al repo de datos
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" className="primary" disabled={readOnly || (!dirty && doc.source === "repo")} title={write.reason} onClick={save}>
                      Guardar…
                    </button>
                    {doc.source === "repo" ? (
                      <button type="button" className="danger" disabled={readOnly} onClick={remove}>
                        Borrar
                      </button>
                    ) : null}
                  </>
                )}
              </div>
            </header>
            {notice ? <p className="render-notice">{notice}</p> : null}
            {!write.canWrite ? <p className="muted">{write.reason}</p> : null}
            <nav className="render-file-tabs" aria-label="Archivos del template">
              {[SPEC_FILE, ...fileNames].map((name) => (
                <button key={name} type="button" className={active === name ? "is-active" : ""} onClick={() => setActive(name)}>
                  {name}
                  {allErrors.some((error) => (error.file ?? SPEC_FILE) === name) ? <span className="dot-error" aria-label="con error" /> : null}
                </button>
              ))}
              {!readOnly ? (
                <span className="render-add-file">
                  <input aria-label="nuevo archivo" placeholder="nuevo.yaml.j2" value={newFile} onChange={(event) => setNewFile(event.target.value)} onKeyDown={(event) => event.key === "Enter" && addFile()} />
                  <button type="button" onClick={addFile}>
                    +
                  </button>
                  {active !== SPEC_FILE ? (
                    <button type="button" className="danger" onClick={removeFile} title={`Quitar ${active}`}>
                      quitar
                    </button>
                  ) : null}
                </span>
              ) : null}
            </nav>
            {active === SPEC_FILE ? (
              <div className="render-spec-controls">
                <label>
                  <span>scope</span>
                  <select disabled={readOnly} value={getTopLevelKey(doc.spec, "scope") || "site"} onChange={(event) => setDoc({ ...doc, spec: setTopLevelKey(doc.spec, "scope", event.target.value) })}>
                    <option value="site">site</option>
                    <option value="cluster">cluster</option>
                    <option value="device">device</option>
                  </select>
                </label>
                <label className="grow">
                  <span>for_each</span>
                  <input
                    disabled={readOnly}
                    value={getTopLevelKey(doc.spec, "for_each")}
                    placeholder="devices | selectattr('kind','equalto','baremetal') | list"
                    onChange={(event) => setDoc({ ...doc, spec: setTopLevelKey(doc.spec, "for_each", event.target.value) })}
                  />
                </label>
                <label className="grow">
                  <span>where</span>
                  <input disabled={readOnly} value={getTopLevelKey(doc.spec, "where")} placeholder="item.interfaces" onChange={(event) => setDoc({ ...doc, spec: setTopLevelKey(doc.spec, "where", event.target.value) })} />
                </label>
                <fieldset className="render-add-output" disabled={readOnly}>
                  <legend>+ salida</legend>
                  <select aria-label="archivo .j2" value={output.template} onChange={(event) => setOutput({ ...output, template: event.target.value })}>
                    <option value="">archivo…</option>
                    {fileNames.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <select aria-label="modo" value={output.mode} onChange={(event) => setOutput({ ...output, mode: event.target.value as "single" | "per_item" })}>
                    <option value="per_item">per_item (un archivo por elemento)</option>
                    <option value="single">single (un archivo)</option>
                  </select>
                  <input aria-label="ruta de salida" value={output.path} onChange={(event) => setOutput({ ...output, path: event.target.value })} />
                  <button type="button" disabled={!output.template || !output.path} onClick={() => setDoc({ ...doc, spec: appendOutput(doc.spec, output) })}>
                    añadir
                  </button>
                </fieldset>
              </div>
            ) : null}
            <CodeEditor
              label={`Contenido de ${active}`}
              value={active === SPEC_FILE ? doc.spec : (doc.files[active] ?? "")}
              readOnly={readOnly}
              onChange={updateActive}
              errorLines={errorLines}
              jumpTo={jump}
            />
          </>
        ) : (
          <p className="muted">Elige un template de la biblioteca o crea uno nuevo.</p>
        )}
      </section>
      <Splitter
        id="split-renders-preview"
        orientation="vertical"
        label="Ancho de la preview"
        size={previewWidth.size}
        spec={PREVIEW_SPEC}
        sign={-1}
        onSize={previewWidth.setSize}
        onReset={previewWidth.reset}
      />
      <section className="render-preview" aria-label="Preview en vivo">
        <div className="render-preview-controls">
          <label>
            <span>sitio</span>
            <select
              value={site}
              onChange={(event) => {
                setSite(event.target.value);
                setCluster("");
                setInstance("");
              }}
            >
              {sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>cluster</span>
            <select
              value={cluster}
              onChange={(event) => {
                setCluster(event.target.value);
                setInstance("");
              }}
            >
              <option value="">—</option>
              {clusters.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>instancia</span>
            <select
              value={instance}
              onChange={(event) => {
                const next = doc?.usedBy.find((item) => item.id === event.target.value);
                setInstance(event.target.value);
                if (next) {
                  setSite(next.site);
                  setCluster(next.cluster ?? "");
                }
              }}
            >
              <option value="">(ninguna)</option>
              {(doc?.usedBy ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </label>
          <label className="grow">
            <span>params (JSON)</span>
            <input value={paramsText} spellCheck={false} onChange={(event) => setParamsText(event.target.value)} title="Sobrescriben los params del template (y de la instancia)" />
          </label>
        </div>
        <nav className="view-tabs render-preview-tabs">
          <button type="button" className={rightTab === "salida" ? "is-active" : ""} onClick={() => setRightTab("salida")}>
            Salida {preview ? `(${preview.count})` : ""}
          </button>
          <button type="button" className={rightTab === "contexto" ? "is-active" : ""} onClick={() => setRightTab("contexto")}>
            Contexto
          </button>
          <span className="muted render-preview-state">{previewing ? "renderizando…" : preview ? (preview.ok && !preview.template_errors.length ? "✔ ok" : "con errores") : ""}</span>
        </nav>
        {rightTab === "salida" ? (
          <div className="render-preview-body">
            {previewError ? <p className="error">{previewError}</p> : null}
            <ErrorList errors={allErrors} warnings={preview?.warnings} onJump={jumpTo} />
            {preview ? (
              <div className="render-output">
                <div className="render-output-tree">
                  <FileTree items={preview.files} selected={selectedOut} onSelect={(file) => setSelectedOut(file.path)} />
                </div>
                <pre className="render-output-content">{selectedFile?.content ?? ""}</pre>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="render-preview-body render-context">
            <p className="muted">
              Variables disponibles en los templates (context v{String(context?.context_version ?? 1)}). Secretos siempre redactados. Ver <code>schemas/render-context.schema.json</code>.
            </p>
            <input className="render-context-search" placeholder="filtrar (p. ej. interfaces, vlan)" value={contextQuery} onChange={(event) => setContextQuery(event.target.value)} />
            {context ? <ValuesTree nodes={valuesToTree(context)} query={contextQuery} emptyLabel="sin contexto" /> : <p className="muted">Sin contexto para este sitio.</p>}
          </div>
        )}
      </section>
      {flow ? (
        <WriteDialog
          flow={flow}
          onClose={() => setFlow(null)}
          onDone={(result) => {
            if (doc && !flow.title.startsWith("Borrar")) setDoc({ ...doc, id: specId, source: "repo", saved: snapshot(doc.spec, doc.files) });
            if (flow.title.startsWith("Borrar")) setDoc(null);
            setNotice(`Guardado en la rama ${result.branch}. Para verlo (y seguir editando esa rama) usa «Ver rama».`);
            onAfterCommit();
          }}
          onViewBranch={(branch) => {
            setFlow(null);
            onViewBranch(branch);
          }}
        />
      ) : null}
    </div>
  );
}

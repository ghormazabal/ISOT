import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import { rendersApi, type ConfigBody } from "../../rendersApi";
import type { RenderInstance, RendersDashboard, RenderTemplateSummary } from "../../rendersTypes";
import type { ClusterIndexEntry, SiteIndexEntry } from "../../types";
import { CodeEditor, errorMessage, StatusBadge, WriteDialog, type WriteFlow, type WriteMode } from "./common";

type Props = {
  refName: string | null;
  version: number;
  sites: SiteIndexEntry[];
  write: WriteMode;
  onAfterCommit: () => void;
  onOpenDiff: (instance: string | null) => void;
  onOpenTemplate: (id: string, source?: "repo" | "bundled") => void;
};

type InstanceDraft = {
  id: string;
  template: string;
  site: string;
  cluster: string;
  target_dir: string;
  dir: string;
  params: string;
  enabled: boolean;
  description: string;
};

const EMPTY_DRAFT: InstanceDraft = { id: "", template: "", site: "", cluster: "", target_dir: "", dir: "", params: "{}", enabled: true, description: "" };

function draftFrom(row: RenderInstance): InstanceDraft {
  return {
    id: row.id,
    template: row.template,
    site: row.site,
    cluster: row.cluster ?? "",
    target_dir: row.target_dir ?? "",
    dir: row.dir_explicit ? row.dir : "",
    params: JSON.stringify(row.params ?? {}, null, 2),
    enabled: row.enabled,
    description: row.description ?? "",
  };
}

function Counter({ label, value, hint, tone, onClick }: { label: string; value: number | string; hint?: string; tone?: "ok" | "warn" | "error"; onClick?: () => void }) {
  const body = (
    <>
      <span className="render-count-value">{value}</span>
      <span className="render-count-label">{label}</span>
      {hint ? <span className="muted render-count-hint">{hint}</span> : null}
    </>
  );
  return onClick ? (
    <button type="button" className={`render-count is-${tone ?? "plain"} is-link`} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className={`render-count is-${tone ?? "plain"}`}>{body}</div>
  );
}

export function RendersSummaryTab({ refName, version, sites, write, onAfterCommit, onOpenDiff, onOpenTemplate }: Props) {
  const [data, setData] = useState<RendersDashboard | null>(null);
  const [templates, setTemplates] = useState<RenderTemplateSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<InstanceDraft | null>(null);
  const [clusters, setClusters] = useState<ClusterIndexEntry[]>([]);
  const [flow, setFlow] = useState<WriteFlow | null>(null);
  const [textMode, setTextMode] = useState<{ text: string; errors: string[] } | null>(null);
  const [settings, setSettings] = useState<{ target_dir: string } | null>(null);
  const [siteFilter, setSiteFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([rendersApi.dashboard(refName), rendersApi.templates(refName)])
      .then(([dash, list]) => {
        if (cancelled) return;
        setData(dash);
        setTemplates(list);
        setSettings({ target_dir: dash.config.target_dir });
      })
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [refName, version]);

  useEffect(() => {
    if (!draft?.site) {
      setClusters([]);
      return;
    }
    api
      .clusters(draft.site)
      .then(setClusters)
      .catch(() => setClusters([]));
  }, [draft?.site]);

  const rows = useMemo(() => (data?.rows ?? []).filter((row) => siteFilter === "all" || row.site === siteFilter), [data, siteFilter]);
  const driftTotal = data ? data.drift.drift : 0;

  const startConfig = (title: string, body: ConfigBody, description?: string) => {
    setFlow({
      title,
      description,
      preview: () => rendersApi.configPreview(body),
      write: (token) => rendersApi.configWrite({ ...body, token }),
    });
  };

  const saveDraft = () => {
    if (!draft) return;
    let params: Record<string, unknown> = {};
    try {
      params = draft.params.trim() ? (JSON.parse(draft.params) as Record<string, unknown>) : {};
    } catch {
      setError("params debe ser JSON válido (p. ej. {\"default_mtu\": 9000})");
      return;
    }
    startConfig(`Instancia ${draft.id}`, {
      upsert_instance: {
        id: draft.id,
        template: draft.template,
        site: draft.site,
        cluster: draft.cluster || null,
        target_dir: draft.target_dir || null,
        dir: draft.dir || undefined,
        params,
        enabled: draft.enabled,
        description: draft.description || null,
      },
      description: `instancia de render ${draft.id}`,
    });
  };

  const openText = async () => {
    try {
      const current = await rendersApi.config(refName);
      setTextMode({ text: current.text || "version: 1\ntarget_dir: rendered\ninstances: []\n", errors: current.errors });
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  if (error && !data) return <div className="renders-body"><p className="error">{error}</p></div>;
  if (!data) return <div className="renders-body"><p className="muted">{loading ? "Cargando renders…" : ""}</p></div>;

  const perSite = Object.entries(data.per_site);
  const perCluster = Object.entries(data.per_cluster);

  return (
    <div className="renders-body renders-summary">
      {error ? <p className="error">{error}</p> : null}
      {data.config.errors.length ? (
        <ul className="render-errors">
          {data.config.errors.map((item, index) => (
            <li key={index} className="is-error">
              <strong>renders.yaml</strong> {item}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="render-counts">
        <Counter label="templates" value={data.templates.total} hint={`${data.templates.repo} del repo · ${data.templates.bundled} incluidos`} tone={data.templates.invalid ? "error" : undefined} />
        <Counter label="instancias" value={data.instances.total} hint={`${data.instances.ok} ok · ${data.instances.error} con error`} tone={data.instances.error ? "error" : "ok"} />
        <Counter label="archivos generados" value={data.files} hint={`context v${data.context_version}`} />
        <Counter label="errores · avisos" value={`${data.errors} · ${data.warnings}`} tone={data.errors ? "error" : data.warnings ? "warn" : "ok"} />
        <Counter
          label="en sync · con drift"
          value={`${data.drift.in_sync} · ${driftTotal}`}
          hint={driftTotal ? "Ver diff contra el destino →" : "destino al día"}
          tone={driftTotal ? "warn" : "ok"}
          onClick={() => onOpenDiff(null)}
        />
      </div>
      {data.drift.error ? (
        <ul className="render-errors">
          <li className="is-warning">
            <strong>carpeta destino</strong> {data.drift.error}
          </li>
        </ul>
      ) : null}

      <div className="render-grid-2">
        <section className="render-card">
          <h3>Por sitio</h3>
          <table className="render-table">
            <thead>
              <tr>
                <th>Sitio</th>
                <th>Instancias</th>
                <th>Archivos</th>
                <th>Errores</th>
              </tr>
            </thead>
            <tbody>
              {perSite.map(([site, item]) => (
                <tr key={site}>
                  <td>{site}</td>
                  <td>{item.instances}</td>
                  <td>{item.files}</td>
                  <td className={item.errors ? "error" : ""}>{item.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="render-card">
          <h3>Por cluster</h3>
          <table className="render-table">
            <thead>
              <tr>
                <th>Cluster</th>
                <th>Instancias</th>
                <th>Archivos</th>
                <th>Errores</th>
              </tr>
            </thead>
            <tbody>
              {perCluster.length ? (
                perCluster.map(([cluster, item]) => (
                  <tr key={cluster}>
                    <td>{cluster}</td>
                    <td>{item.instances}</td>
                    <td>{item.files}</td>
                    <td className={item.errors ? "error" : ""}>{item.errors}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="muted">
                    Ninguna instancia con cluster.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </div>

      <section className="render-card">
        <header className="render-card-head">
          <h3>Instancias ({rows.length})</h3>
          <label className="site-picker">
            <span>Sitio</span>
            <select value={siteFilter} onChange={(event) => setSiteFilter(event.target.value)}>
              <option value="all">Todos</option>
              {sites.map((site) => (
                <option key={site.id} value={site.id}>
                  {site.name}
                </option>
              ))}
            </select>
          </label>
          <div className="wizard-actions">
            <button type="button" disabled={!write.canWrite} title={write.reason} onClick={() => setDraft({ ...EMPTY_DRAFT, site: siteFilter === "all" ? sites[0]?.id ?? "" : siteFilter, template: templates[0]?.id ?? "" })}>
              + Nueva instancia
            </button>
            <button type="button" onClick={openText}>
              renders.yaml como texto
            </button>
          </div>
        </header>
        {rows.length ? (
          <table className="render-table">
            <thead>
              <tr>
                <th>Instancia</th>
                <th>Template</th>
                <th>Sitio / cluster</th>
                <th>Carpeta destino</th>
                <th>Archivos</th>
                <th>Estado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <strong>{row.id}</strong>
                    {row.description ? <div className="muted">{row.description}</div> : null}
                  </td>
                  <td>
                    <button type="button" className="link-button" onClick={() => onOpenTemplate(row.template, row.template_source === "repo" ? "repo" : undefined)}>
                      {row.template}
                    </button>{" "}
                    <span className="muted">
                      v{row.version} · {row.template_source ?? "?"}
                    </span>
                  </td>
                  <td>
                    {row.site}
                    {row.cluster ? ` / ${row.cluster}` : ""}
                  </td>
                  <td>
                    <code>{row.folder ?? "—"}</code>
                    {row.folder_error ? <div className="error render-row-error">{row.folder_error}</div> : null}
                  </td>
                  <td>{row.files}</td>
                  <td>
                    <StatusBadge status={row.status} />
                    {row.error_count ? <span className="error"> {row.error_count} err</span> : null}
                    {row.warning_count ? <span className="warn"> {row.warning_count} aviso</span> : null}
                    {row.errors[0] ? <div className="muted render-row-error">{row.errors[0].file}{row.errors[0].line ? `:${row.errors[0].line}` : ""} {row.errors[0].message}</div> : null}
                  </td>
                  <td className="render-row-actions">
                    <button type="button" className="link-button" onClick={() => onOpenDiff(row.id)}>
                      diff
                    </button>
                    <button type="button" className="link-button" disabled={!write.canWrite} onClick={() => setDraft(draftFrom(row))}>
                      editar
                    </button>
                    <button
                      type="button"
                      className="link-button danger"
                      disabled={!write.canWrite}
                      onClick={() => startConfig(`Quitar instancia ${row.id}`, { remove_instance: row.id, description: `quitar instancia de render ${row.id}` }, "Solo se quita de renders.yaml; los archivos ya generados en el destino quedan como huérfanos hasta aplicar con «borrar huérfanos».")}
                    >
                      quitar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">
            No hay instancias. Una instancia = template + sitio (+ cluster) + params. Créala con «+ Nueva instancia» o desde la pestaña Plantillas.
          </p>
        )}
      </section>

      {draft ? (
        <section className="render-card render-form">
          <h3>{data.rows.some((row) => row.id === draft.id) ? `Editar instancia ${draft.id}` : "Nueva instancia"}</h3>
          <div className="render-form-grid">
            <label>
              <span>id</span>
              <input value={draft.id} placeholder="site1-netplan" onChange={(event) => setDraft({ ...draft, id: event.target.value })} />
            </label>
            <label>
              <span>template</span>
              <select value={draft.template} onChange={(event) => setDraft({ ...draft, template: event.target.value })}>
                {templates
                  .filter((item) => item.source !== "bundled-overridden")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} ({item.source === "repo" ? "repo" : "incluido"}, {item.scope})
                    </option>
                  ))}
              </select>
            </label>
            <label>
              <span>sitio</span>
              <select value={draft.site} onChange={(event) => setDraft({ ...draft, site: event.target.value, cluster: "" })}>
                {sites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>cluster</span>
              <select value={draft.cluster} onChange={(event) => setDraft({ ...draft, cluster: event.target.value })}>
                <option value="">(ninguno: alcance sitio)</option>
                {clusters.map((cluster) => (
                  <option key={cluster.name} value={cluster.name}>
                    {cluster.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>carpeta destino (opcional, en este repo)</span>
              <input value={draft.target_dir} placeholder={data.config.target_dir} onChange={(event) => setDraft({ ...draft, target_dir: event.target.value })} />
            </label>
            <label>
              <span>subcarpeta (opcional)</span>
              <input value={draft.dir} placeholder="<sitio>/<cluster o site>/<template>" onChange={(event) => setDraft({ ...draft, dir: event.target.value })} />
            </label>
            <label className="span-2">
              <span>descripción</span>
              <input value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            </label>
            <label className="span-2">
              <span>params (JSON, sobrescriben los defaults del template)</span>
              <textarea rows={4} value={draft.params} spellCheck={false} onChange={(event) => setDraft({ ...draft, params: event.target.value })} />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
              <span>habilitada</span>
            </label>
          </div>
          <div className="wizard-actions">
            <button type="button" className="primary" disabled={!draft.id || !draft.template || !draft.site} onClick={saveDraft}>
              Ver cambios en renders.yaml…
            </button>
            <button type="button" onClick={() => setDraft(null)}>
              Cancelar
            </button>
          </div>
        </section>
      ) : null}

      {settings ? (
        <section className="render-card render-form">
          <h3>Carpeta destino</h3>
          <p className="muted">
            Los renders se comparan (y se aplican) contra <code>{"<target_dir>/<sitio>/<clúster o site>/<template>"}</code> de <strong>este mismo repo de datos</strong>;
            cada instancia puede usar otra carpeta con su propio <code>target_dir</code>. ISoT nunca mueve ni escribe nada de un repositorio a otro, ni dentro de su instalación.
          </p>
          <div className="render-form-grid">
            <label>
              <span>target_dir</span>
              <input value={settings.target_dir} onChange={(event) => setSettings({ ...settings, target_dir: event.target.value })} />
            </label>
          </div>
          <div className="wizard-actions">
            <button
              type="button"
              disabled={!write.canWrite || settings.target_dir === data.config.target_dir}
              title={write.reason}
              onClick={() => startConfig("Carpeta destino", { settings, description: "carpeta destino de renders" })}
            >
              Ver cambios…
            </button>
          </div>
        </section>
      ) : null}

      {textMode ? (
        <section className="render-card">
          <header className="render-card-head">
            <h3>renders.yaml</h3>
            <span className="muted">Edición libre (conserva comentarios). Esquema: schemas/renders.schema.json</span>
          </header>
          <CodeEditor label="renders.yaml" value={textMode.text} readOnly={!write.canWrite} onChange={(text) => setTextMode({ ...textMode, text })} />
          <div className="wizard-actions">
            <button type="button" className="primary" disabled={!write.canWrite} title={write.reason} onClick={() => startConfig("renders.yaml", { text: textMode.text, description: "renders.yaml" })}>
              Ver cambios…
            </button>
            <button type="button" onClick={() => setTextMode(null)}>
              Cerrar
            </button>
          </div>
        </section>
      ) : null}

      {flow ? (
        <WriteDialog
          flow={flow}
          onClose={() => setFlow(null)}
          onDone={() => {
            setDraft(null);
            setTextMode(null);
            onAfterCommit();
          }}
        />
      ) : null}
    </div>
  );
}

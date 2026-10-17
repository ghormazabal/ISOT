import { useEffect, useState } from "react";

import { rendersApi } from "../../rendersApi";
import type { SaveBody, SavePlan, SaveResult, SaveStatus } from "../../rendersTypes";
import { errorMessage, StatusBadge, UnifiedDiff } from "./common";

type Props = {
  body: SaveBody;
  status: SaveStatus | null;
  onClose: () => void;
  onDone: (result: SaveResult) => void;
  /** Revisar el resultado: Diff con base = rama nueva y head = Local, o Comparar base → rama. */
  onReview: (refs: { base: string; head: string }) => void;
};

const ACTION_LABEL: Record<string, string> = { write: "se escribe", delete: "se borra", keep: "sin cambios" };

/**
 * «Guardar en el repo» desde la preview: igual que Diff → Aplicar para esta instancia.
 * Muestra destino, estado y diff por archivo; commit en una rama wizard/… del mismo repo.
 */
export function SaveOutputDialog({ body, status, onClose, onDone, onReview }: Props) {
  const needsInstance = Boolean(status?.needs_instance);
  const [createInstance, setCreateInstance] = useState(false);
  const [newId, setNewId] = useState(status?.new_instance?.id ?? "");
  const [prune, setPrune] = useState(false);
  const [message, setMessage] = useState("");
  const [plan, setPlan] = useState<SavePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SaveResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const request: SaveBody = {
    ...body,
    prune,
    ...(needsInstance && createInstance ? { create_instance: true, new_instance_id: newId.trim() || undefined } : {}),
  };
  const ready = !needsInstance || createInstance;

  useEffect(() => {
    if (!ready || result) return;
    let cancelled = false;
    setError(null);
    setPlan(null);
    const timer = window.setTimeout(() => {
      rendersApi
        .savePreview(request)
        .then((value) => {
          if (cancelled) return;
          setPlan(value);
          setOpen(value.files.find((file) => file.action !== "keep")?.path ?? null);
        })
        .catch((err: unknown) => !cancelled && setError(errorMessage(err)));
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, createInstance, newId, prune]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const value = await rendersApi.saveCommit({ ...request, message: message.trim() || undefined });
      setResult(value);
      onDone(value);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const orphans = plan?.files.filter((file) => file.status === "orphaned").length ?? 0;
  const counts = plan
    ? plan.files.reduce<Record<string, number>>((acc, file) => ({ ...acc, [file.status]: (acc[file.status] ?? 0) + 1 }), {})
    : {};

  return (
    <div className="render-modal" role="dialog" aria-modal="true" aria-label="Guardar la salida en el repo" id="save-output-dialog">
      <div className="render-modal-box">
        <header>
          <h3>Guardar la salida en el repo</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>
        <p className="muted">
          Escribe lo que renderiza <code>{body.template}</code> para <code>{body.site}{body.cluster ? `/${body.cluster}` : ""}</code> en su carpeta destino de
          este mismo repo de datos, con su <code>.isot-render.json</code>, en una rama <code>wizard/…</code> nueva
          {body.branch ? <> (continúa <code>{body.branch}</code>)</> : null}. El índice se regenera en el mismo commit. Nunca escribe en main ni en otro repo.
        </p>

        {needsInstance && !result ? (
          <div className="save-instance-box">
            <p>
              <strong>Hace falta una instancia.</strong> {status?.reason}. La salida se guarda siempre a partir de una instancia de{" "}
              <code>renders.yaml</code> (template + sitio/clúster + params), para que Diff, Aplicar y la CI la reproduzcan igual.
            </p>
            <label className="save-instance-id">
              <span>id de la instancia nueva</span>
              <input value={newId} spellCheck={false} onChange={(event) => setNewId(event.target.value)} disabled={busy} />
            </label>
            {status?.new_instance?.params ? (
              <p className="muted">
                params: <code>{JSON.stringify(status.new_instance.params)}</code>
              </p>
            ) : null}
            <label className="checkbox">
              <input type="checkbox" id="save-create-instance" checked={createInstance} onChange={(event) => setCreateInstance(event.target.checked)} />
              <span>Crear instancia con estos parámetros (se añade a renders.yaml en el mismo commit)</span>
            </label>
          </div>
        ) : null}

        {error ? <p className="error">{error}</p> : null}
        {ready && !plan && !error && !result ? <p className="muted">Calculando cambios…</p> : null}

        {plan && !result ? (
          <>
            <p>
              Destino: <code>{plan.dir}/</code> · instancia <code>{plan.instance}</code>
              {plan.new_instance ? " (nueva)" : ""}
            </p>
            <div className="render-diff-summary">
              {(["new", "modified", "unchanged", "orphaned"] as const).map((key) =>
                counts[key] ? (
                  <span key={key} className={`render-status is-${key}`}>
                    {counts[key]} {key === "new" ? "nuevos" : key === "modified" ? "modificados" : key === "orphaned" ? "huérfanos" : "iguales"}
                  </span>
                ) : null,
              )}
            </div>
            <div className="render-modal-files">
              {plan.files.map((file) => (
                <details key={file.path} open={open === file.path} onToggle={(event) => event.currentTarget.open && setOpen(file.path)}>
                  <summary>
                    <StatusBadge status={file.status} /> <code>{file.path}</code> <span className="muted">· {ACTION_LABEL[file.action]}</span>
                  </summary>
                  {file.diff ? <UnifiedDiff diff={file.diff} /> : <p className="muted">Igual que en el repo.</p>}
                </details>
              ))}
              {plan.extra.map((file) => (
                <details key={file.path} open={open === file.path} onToggle={(event) => event.currentTarget.open && setOpen(file.path)}>
                  <summary>
                    <StatusBadge status={file.status} /> <code>{file.path}</code> <span className="muted">· se escribe</span>
                  </summary>
                  <UnifiedDiff diff={file.diff} />
                </details>
              ))}
            </div>
            {orphans ? (
              <label className="checkbox">
                <input type="checkbox" checked={prune} onChange={(event) => setPrune(event.target.checked)} />
                <span>borrar los {orphans} huérfano(s) (están en el destino pero el render ya no los genera)</span>
              </label>
            ) : null}
            <label className="save-message">
              <span>Mensaje del commit (opcional)</span>
              <input id="save-message" value={message} placeholder={`renders: guardar ${plan.instance}`} onChange={(event) => setMessage(event.target.value)} />
            </label>
            {!plan.changes ? <p className="muted">La carpeta destino ya está al día: no hay nada que guardar.</p> : null}
          </>
        ) : null}

        {result ? (
          <div className="render-result" id="save-output-result">
            <p>
              ✔ Commit <code>{result.short}</code> en la rama <code>{result.branch}</code>
              {result.created ? " (nueva)" : " (continuada)"} · destino <code>{result.dir}/</code>
              {result.created_instance ? <> · instancia <code>{result.instance}</code> añadida a renders.yaml</> : null}.
            </p>
            {result.push ? <p className={result.push.ok ? "muted" : "error"}>Push: {result.push.message}</p> : null}
            <p className="muted">Tu working tree sigue en {result.base}. Revisa la rama y ábrela como PR/MR desde tu flujo Git habitual.</p>
          </div>
        ) : null}

        <div className="wizard-actions">
          {result ? (
            <>
              <button type="button" className="primary" onClick={() => onReview({ base: result.branch, head: "local" })} title="Diff con base = la rama nueva y head = Local">
                Ver en Diff (rama → Local)
              </button>
              <button type="button" onClick={() => onReview({ base: result.base, head: result.branch })} title={`Comparar ${result.base} → ${result.branch}`}>
                Comparar {result.base} → rama
              </button>
              <button type="button" onClick={onClose}>
                Cerrar
              </button>
            </>
          ) : (
            <>
              <button type="button" className="primary" id="save-confirm" disabled={busy || !plan || !plan.changes} onClick={confirm}>
                {busy ? "Guardando…" : "Confirmar y hacer commit"}
              </button>
              <button type="button" onClick={onClose}>
                Cancelar
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

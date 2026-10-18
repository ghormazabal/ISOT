import { useEffect, useState } from "react";

import { isConflict, notifyWorktreeChanged, openChanges } from "../../api";
import { rendersApi } from "../../rendersApi";
import type { SaveBody, SavePlan, SaveResult, SaveStatus } from "../../rendersTypes";
import { CONFLICT_NOTICE, errorMessage, PendingNote, StatusBadge, UnifiedDiff } from "./common";

type Props = {
  body: SaveBody;
  status: SaveStatus | null;
  onClose: () => void;
  onDone: (result: SaveResult) => void;
  /** Revisar lo guardado: Comparar con base = la rama actual (HEAD) y head = Local (working tree). */
  onReview: (refs: { base: string; head: string }) => void;
};

const ACTION_LABEL: Record<string, string> = { write: "se escribe", delete: "se borra", keep: "sin cambios" };

/**
 * «Guardar en el repo» desde la preview: igual que Diff → Aplicar para esta instancia.
 * Muestra destino, estado y diff por archivo y escribe en el working tree (sin commit);
 * el commit es aparte, desde «Cambios» en la barra superior.
 */
export function SaveOutputDialog({ body, status, onClose, onDone, onReview }: Props) {
  const needsInstance = Boolean(status?.needs_instance);
  const [createInstance, setCreateInstance] = useState(false);
  const [newId, setNewId] = useState(status?.new_instance?.id ?? "");
  const [prune, setPrune] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
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
  }, [ready, createInstance, newId, prune, reload]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const value = await rendersApi.saveWrite({ ...request, token: plan?.token });
      setResult(value);
      notifyWorktreeChanged();
      onDone(value);
    } catch (err) {
      if (isConflict(err)) {
        // El working tree cambió entre la vista previa y el guardado: se recalcula el diff.
        setNotice(CONFLICT_NOTICE);
        setReload((value) => value + 1);
      } else {
        setError(errorMessage(err));
      }
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
          este mismo repo de datos, con su <code>.isot-render.json</code>, en el <strong>working tree local</strong> (sin commit). Puedes
          guardar varias cosas y luego hacer <strong>un</strong> commit desde <strong>Cambios</strong> (barra superior), en una rama{" "}
          <code>wizard/…</code>. Nunca escribe en otro repo.
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
              <span>Crear instancia con estos parámetros (se añade a renders.yaml, en el mismo guardado)</span>
            </label>
          </div>
        ) : null}

        {notice ? <p className="pending-warning is-conflict" id="save-conflict">{notice}</p> : null}
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
                    {file.pending ? <span className="pending-flag"> ⚠ sin commit</span> : null}
                  </summary>
                  <PendingNote pending={file.pending} />
                  {file.diff ? <UnifiedDiff diff={file.diff} /> : <p className="muted">Igual que en el repo.</p>}
                </details>
              ))}
              {plan.extra.map((file) => (
                <details key={file.path} open={open === file.path} onToggle={(event) => event.currentTarget.open && setOpen(file.path)}>
                  <summary>
                    <StatusBadge status={file.status} /> <code>{file.path}</code> <span className="muted">· se escribe</span>
                    {file.pending ? <span className="pending-flag"> ⚠ sin commit</span> : null}
                  </summary>
                  <PendingNote pending={file.pending} />
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
            {!plan.changes ? <p className="muted">La carpeta destino ya está al día: no hay nada que guardar.</p> : null}
          </>
        ) : null}

        {result ? (
          <div className="render-result" id="save-output-result">
            <p>
              ✔ Guardado en el working tree ({result.files.length} archivo(s), <strong>sin commit</strong>) · destino{" "}
              <code>{result.dir}/</code>
              {result.created_instance ? <> · instancia <code>{result.instance}</code> añadida a renders.yaml</> : null}.
            </p>
            <p className="muted">
              Cambios pendientes en <code>{result.branch}</code>: {result.pending}. Sigue guardando lo que necesites y haz un solo commit
              desde <strong>Cambios</strong>.
            </p>
          </div>
        ) : null}

        <div className="wizard-actions">
          {result ? (
            <>
              <button
                type="button"
                className="primary"
                id="save-open-changes"
                onClick={() => {
                  onClose();
                  openChanges();
                }}
              >
                Abrir Cambios (commit)
              </button>
              <button type="button" onClick={() => onReview({ base: result.branch, head: "local" })} title={`Comparar ${result.branch} (HEAD) → Local: lo guardado sin commit`}>
                Ver en Comparar ({result.branch} → Local)
              </button>
              <button type="button" onClick={onClose}>
                Cerrar
              </button>
            </>
          ) : (
            <>
              <button type="button" className="primary" id="save-confirm" disabled={busy || !plan || !plan.changes} onClick={confirm}>
                {busy ? "Guardando…" : "Guardar en el working tree"}
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

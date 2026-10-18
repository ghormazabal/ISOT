import { useCallback, useEffect, useMemo, useState } from "react";

import { api, notifyWorktreeChanged, OPEN_CHANGES_EVENT, WORKTREE_EVENT } from "../api";
import type { WorktreeCommitResult, WorktreeDiff, WorktreeStatus } from "../types";
import { commitBlockReason, commitTargets, groupByArea, mergeSelection, newBranchName, toggle, WORKTREE_STATUS_LABEL } from "../worktree";
import { splitPath } from "../renders";
import { UnifiedDiff } from "./renders/common";

type Props = {
  /** Abrir Comparar (p. ej. main → rama recién commiteada). */
  onCompare: (base: string, head: string) => void;
  /** Tras descartar o hacer commit (refrescar datos, rama del repo…). */
  onChanged: () => void;
};

const POLL_MS = 15000;

/** «Cambios (N)» en la barra superior: lo guardado sin commit en el working tree y el commit aparte. */
export function ChangesControl({ onCompare, onChanged }: Props) {
  const [status, setStatus] = useState<WorktreeStatus | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    api
      .worktree()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    load();
    const onOpen = () => {
      load();
      setOpen(true);
    };
    window.addEventListener(WORKTREE_EVENT, load);
    window.addEventListener(OPEN_CHANGES_EVENT, onOpen);
    window.addEventListener("focus", load);
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      window.removeEventListener(WORKTREE_EVENT, load);
      window.removeEventListener(OPEN_CHANGES_EVENT, onOpen);
      window.removeEventListener("focus", load);
      window.clearInterval(timer);
    };
  }, [load]);

  const count = status ? status.pending || status.count : 0;
  return (
    <>
      <button
          type="button"
          id="changes-toggle"
          className={`changes-button${count ? " has-changes" : ""}${open ? " is-active" : ""}`}
          aria-pressed={open}
          title={status ? `${status.count} archivo(s) sin commit en ${status.branch}` : "Cambios sin commit del repo de datos"}
          onClick={() => {
            load();
            setOpen((value) => !value);
          }}
        >
          Cambios <span className="changes-count" id="changes-count">{count}</span>
        </button>
      {open && status ? (
        <ChangesPanel
          status={status}
          onReload={load}
          onClose={() => setOpen(false)}
          onChanged={() => {
            load();
            notifyWorktreeChanged();
            onChanged();
          }}
          onCompare={(base, head) => {
            setOpen(false);
            onCompare(base, head);
          }}
        />
      ) : null}
    </>
  );
}

function ChangesPanel({
  status,
  onReload,
  onClose,
  onChanged,
  onCompare,
}: {
  status: WorktreeStatus;
  onReload: () => void;
  onClose: () => void;
  onChanged: () => void;
  onCompare: (base: string, head: string) => void;
}) {
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [known, setKnown] = useState(status.files);
  const [focus, setFocus] = useState<string | null>(null);
  const [diff, setDiff] = useState<WorktreeDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const targets = commitTargets(status);
  const [target, setTarget] = useState<"current" | "new">(targets.defaultTarget);
  const [branchName, setBranchName] = useState("");
  const [stay, setStay] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorktreeCommitResult | null>(null);

  // Al recargar el estado se conserva la selección (lo nuevo entra marcado).
  useEffect(() => {
    setSelected((previous) => mergeSelection(previous, known, status.files));
    setKnown(status.files);
    setFocus((current) => (current && status.files.some((file) => file.path === current) ? current : (status.files[0]?.path ?? null)));
    if (!targets.current) setTarget("new");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  useEffect(() => {
    if (!focus) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffError(null);
    api
      .worktreeDiff(focus)
      .then((value) => !cancelled && setDiff(value))
      .catch((err: unknown) => {
        if (cancelled) return;
        setDiff(null);
        setDiffError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [focus, status]);

  const groups = useMemo(() => groupByArea(status.files), [status.files]);
  const chosen = selected ?? new Set<string>();
  const pendingFiles = status.files.filter((file) => file.area !== "index");
  const chosenCount = pendingFiles.filter((file) => chosen.has(file.path)).length;
  const indexCount = status.files.length - pendingFiles.length;
  const blocked = commitBlockReason({
    edit: Boolean(status.edit),
    selected: chosenCount,
    pending: pendingFiles.length,
    indexOnly: indexCount > 0,
    target,
    branch: status.branch,
  });
  const branchPreview = target === "current" ? status.branch : newBranchName(branchName, message, chosenCount);

  const discard = async (path: string) => {
    if (!window.confirm(`¿Descartar los cambios sin commit de ${path}? Vuelve a como está en ${status.branch} (si es nuevo, se borra). No se puede deshacer.`)) return;
    setError(null);
    try {
      const token = diff && diff.path === path ? { [path]: diff.token } : undefined;
      await api.worktreeDiscard([path], token);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      onReload();
    }
  };

  const commit = async () => {
    setBusy(true);
    setError(null);
    try {
      const value = await api.worktreeCommit({
        paths: pendingFiles.filter((file) => chosen.has(file.path)).map((file) => file.path),
        message,
        target,
        branch_name: target === "new" ? branchName.trim() || undefined : undefined,
        stay,
      });
      setResult(value);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="render-modal changes-modal" role="dialog" aria-modal="true" aria-label="Cambios sin commit" id="changes-panel">
      <div className="render-modal-box changes-box">
        <header>
          <h3>
            Cambios sin commit · <code>{status.branch}</code>
          </h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>
        <p className="muted">
          Lo que «Guardar» dejó en el working tree del repo de datos (y cualquier otro cambio local). Elige qué entra en el commit; el índice se
          regenera justo antes y se incluye solo. Nunca se hace commit en main/master ni se hace push.
        </p>
        {error ? <p className="error">{error}</p> : null}
        {result ? (
          <div className="render-result" id="changes-result">
            <p>
              ✔ Commit <code>{result.short}</code> en <code>{result.branch}</code> {result.created ? `(nueva, desde ${result.base})` : "(continuada)"} ·{" "}
              {result.files.length} archivo(s){result.index_files.length ? ` + ${result.index_files.length} de índice` : ""}.
            </p>
            <p className="muted">
              Ahora estás en <code>{result.current_branch}</code>.{result.note ? ` ${result.note}.` : ""} Quedan {result.pending} cambio(s) sin commit.
              Revisa la rama y ábrela como PR/MR con tu flujo Git habitual (ISoT no hace push).
            </p>
            <div className="wizard-actions">
              <button type="button" className="primary" id="changes-compare" onClick={() => onCompare(result.base, result.branch)}>
                Comparar {result.base} → {result.branch}
              </button>
              <button type="button" onClick={onClose}>
                Cerrar
              </button>
            </div>
          </div>
        ) : !status.files.length ? (
          <p className="muted" id="changes-empty">
            No hay cambios sin commit. Usa «Guardar…» en Renders, Aplicar, los wizards o Corregir; todo queda aquí hasta que hagas commit.
          </p>
        ) : (
          <div className="changes-layout">
            <div className="changes-list" id="changes-list">
              {groups.map((group) => {
                const paths = group.files.map((file) => file.path);
                const all = group.id !== "index" && paths.every((path) => chosen.has(path));
                return (
                  <section key={group.id} className="changes-group">
                    <h4>
                      {group.id === "index" ? (
                        <span title="Se regenera justo antes del commit y entra solo">
                          {group.label} <span className="muted">· se regenera e incluye solo</span>
                        </span>
                      ) : (
                        <label className="checkbox">
                          <input type="checkbox" checked={all} onChange={(event) => setSelected(toggle(chosen, paths, event.target.checked))} />
                          <span>
                            {group.label} <span className="muted">({group.files.length})</span>
                          </span>
                        </label>
                      )}
                    </h4>
                    <ul>
                      {group.files.map((file) => (
                        <li key={file.path} className={`changes-file${focus === file.path ? " is-active" : ""}`}>
                          {file.area !== "index" ? (
                            <input
                              type="checkbox"
                              aria-label={`incluir ${file.path}`}
                              checked={chosen.has(file.path)}
                              onChange={(event) => setSelected(toggle(chosen, [file.path], event.target.checked))}
                            />
                          ) : (
                            <span className="changes-auto">auto</span>
                          )}
                          <button type="button" className="changes-path" title={file.path} onClick={() => setFocus(file.path)}>
                            <span className={`render-status is-wt-${file.status}`}>{WORKTREE_STATUS_LABEL[file.status] ?? file.status}</span>
                            <span className="file-path">
                              {splitPath(file.path).dir ? <span className="file-dir">{splitPath(file.path).dir}</span> : null}
                              <span className="file-base">{splitPath(file.path).name}</span>
                            </span>
                          </button>
                          <button type="button" className="link-button changes-discard" title="Descartar los cambios de este archivo" onClick={() => void discard(file.path)} disabled={!status.edit}>
                            Descartar
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
            <div className="changes-diff" id="changes-diff">
              {focus ? (
                <>
                  <p className="mono changes-diff-path">{focus}</p>
                  {diffError ? <p className="error">{diffError}</p> : null}
                  {diff ? diff.binary ? <p className="muted">(binario)</p> : <UnifiedDiff diff={diff.diff} /> : !diffError ? <p className="muted">Cargando diff…</p> : null}
                </>
              ) : null}
            </div>
          </div>
        )}
        {!result && status.files.length ? (
          <div className="changes-commit">
            <label className="save-message">
              <span>Mensaje del commit</span>
              <input id="changes-message" value={message} placeholder={`isot: ${chosenCount} archivo(s)`} onChange={(event) => setMessage(event.target.value)} />
            </label>
            <div className="changes-target" role="radiogroup" aria-label="Rama destino">
              <label className="checkbox">
                <input type="radio" name="changes-target" checked={target === "current"} disabled={!targets.current} onChange={() => setTarget("current")} />
                <span>
                  En la rama actual <code>{status.branch}</code>
                  {!targets.current ? <span className="muted"> (no: nunca en {status.branch})</span> : null}
                </span>
              </label>
              <label className="checkbox">
                <input type="radio" name="changes-target" id="changes-target-new" checked={target === "new"} onChange={() => setTarget("new")} />
                <span>Rama nueva desde {status.head ? <code>{status.head}</code> : "HEAD"}</span>
              </label>
              {target === "new" ? (
                <>
                  <label className="save-message">
                    <span>Nombre (opcional; si no, a partir del mensaje)</span>
                    <input id="changes-branch" value={branchName} placeholder="p. ej. renders site1" onChange={(event) => setBranchName(event.target.value)} />
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={stay} onChange={(event) => setStay(event.target.checked)} />
                    <span>Seguir trabajando en la rama nueva (si no, se vuelve a {status.branch})</span>
                  </label>
                </>
              ) : null}
            </div>
            <div className="wizard-actions">
              <button type="button" className="primary" id="changes-commit" disabled={busy || Boolean(blocked)} title={blocked ?? undefined} onClick={() => void commit()}>
                {busy ? "Haciendo commit…" : `Commit (${chosenCount} archivo(s) + índice) → ${branchPreview}`}
              </button>
              <button type="button" onClick={onClose}>
                Cerrar
              </button>
              {blocked ? <span className="muted">{blocked}</span> : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
